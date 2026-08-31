"""The session host, bound to one campaign.

Runs inside the DM's app or inside the headless ``canonkeeper-server``; the class
does not know or care which. It owns the roster, rolls the dice, rebroadcasts
chat, and -- the part that matters -- decides what each logged-in player is
allowed to see.

Clients are untrusted. Every outbound entity goes through
:mod:`canon_keeper.net.projection` first, so a player's app is never sent a
secret and asked to hide it.

Logging in is a challenge/response: the password never crosses the wire. See
:mod:`canon_keeper_protocol.auth`.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from canon_keeper import campaigns
from canon_keeper.content import Content
from canon_keeper.net import discovery
from canon_keeper_protocol import auth
from canon_keeper.repo.chat import (
    DEFAULT_LIMIT,
    DM_ONLY,
    EVERYONE,
    ROLLED,
    SAID,
    SYSTEM,
)
from canon_keeper_protocol.dice import DiceError, roll
from canon_keeper.repo.entities import StaleWrite
from canon_keeper.rules.validation import validate
from canon_keeper.net.projection import (
    project_encounter,
    project_facts,
    EditRefused,
    changed_sheet_fields,
    snapshot_since,
    Viewer,
    apply_player_edit,
    project_entity,
    snapshot,
    visible_entity_ids,
)
from canon_keeper.repo.encounters import DEFAULT_HEIGHT, DEFAULT_WIDTH
from canon_keeper.rules import attack
from canon_keeper_protocol.messages import (
    MAX_CHAT_LENGTH,
    MAX_NAME_LENGTH,
    MAX_NOTATION_LENGTH,
    Member,
    MessageType,
    ProtocolError,
    Role,
    SystemKind,
    clean_name,
    decode,
    encode,
    new_member_id,
)

log = logging.getLogger("canonkeeper.net.server")

DEFAULT_PORT = 8765

#: A socket that has not finished logging in by then is closed. Stops a port
#: scanner or a stalled client from occupying a slot indefinitely.
LOGIN_TIMEOUT_MS = 20_000


@dataclass
class _Pending:
    """A connection that has said hello but not yet proved who it is."""

    timer: QTimer
    username: str = ""
    nonce: bytes = b""
    account_id: int | None = None
    attempts: int = 0
    #: Versions the client already holds, so the first reply can be a delta
    #: rather than the whole campaign.
    known: dict[int, int] = field(default_factory=dict)


@dataclass
class _Session:
    """A logged-in connection."""

    member: Member
    account_id: int | None
    viewer: Viewer
    #: An autopilot login. Its chat is refused while autopilot is off, which is
    #: the whole of what "off" means -- not a politeness the agent observes.
    is_agent: bool = False
    #: Composing something right now. Broadcast so a table can see that
    #: silence means thinking rather than nothing happening.
    busy: bool = False
    visible: set[int] = field(default_factory=set)
    #: What version of each entity we last sent this connection. The base for
    #: any edit they send back, because a client must not be able to choose
    #: which version it claims to be editing -- nor to omit it and get an
    #: unconditional write.
    sent: dict[int, int] = field(default_factory=dict)

    def remember_sent(self, projected: list[dict] | dict) -> None:
        entries = projected if isinstance(projected, list) else [projected]
        for entity in entries:
            if isinstance(entity.get("id"), int) and isinstance(
                entity.get("version"), int
            ):
                self.sent[entity["id"]] = entity["version"]


class SessionServer(QObject):
    started = Signal(int)  # port
    stopped = Signal()
    failed = Signal(str)
    roster_changed = Signal(list)  # list[Member]
    #: A player's edit was applied. The DM's own panels read the database
    #: directly, so without this their screen shows yesterday's hit points.
    entity_applied = Signal(int)
    #: Something moved a token that was not the DM's own panel -- today only an
    #: agent on autopilot. Same reason as above: the DM's map is read from the
    #: database, so it does not know until it is told.
    encounter_applied = Signal()

    def __init__(
        self,
        repos,
        campaign_id: int,
        session_name: str = "Canon Keeper session",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.repos = repos
        self.campaign_id = campaign_id
        self.session_name = session_name

        #: Lets the DM's own app authenticate as itself without a password. It
        #: already holds the campaign file, so demanding a login on the same
        #: machine is friction with nothing behind it. The token is regenerated
        #: per run and never leaves the process except to the local client.
        self.local_token = secrets.token_urlsafe(32)

        #: A campaign's own identity, generated once and kept in its settings.
        #: Entity ids and versions both restart at one in a new campaign, so
        #: without this a client's cache from a different campaign looks
        #: perfectly up to date and is served back to them unchanged.
        self.campaign_key = self._campaign_key()
        #: The evening this is. Chat is filed against it, so each session is
        #: its own log rather than one endless scroll.
        self.session_id = self._open_session()
        #: For checking what a player proposes. A sheet is validated here,
        #: on the host, because the client that sent it is the one thing we
        #: cannot take at its word.
        self.content = Content(repos.settings)

        self._server: QWebSocketServer | None = None
        self._sessions: dict[QWebSocket, _Session] = {}
        # Runtime only, never persisted. Reopening a campaign and finding a
        # machine already running your table is not a state anyone should
        # arrive in by default: autopilot is switched on deliberately, each
        # time, by someone in the room.
        self._autopilot = False
        self._autopilot_by = ""
        #: What the agent reports having spent. Runtime only, like autopilot:
        #: it is "this session's bill", not a total anyone is accruing.
        self._spend: dict = {}
        #: Turns waiting on the player whose character they belong to, by id.
        #: Runtime only: a proposal nobody answered before the session closed
        #: is a proposal about a moment that has passed.
        self._proposed: dict[str, dict] = {}
        self._pending: dict[QWebSocket, _Pending] = {}
        self._beacon = discovery.Beacon(self)

    def _open_session(self) -> int | None:
        try:
            return self.repos.sessions.ensure_open(self.campaign_id).id
        except Exception:  # noqa: BLE001 - a log is not worth failing to host
            log.exception("could not open a session for the chat log")
            return None

    def _record(self, kind: str, text: str, speaker: str = "", role: str = "",
                payload: dict | None = None, audience: str = EVERYONE) -> None:
        """Keep what was said. Never let the log stop the game."""
        try:
            self.repos.chat.add(
                self.campaign_id,
                kind,
                text,
                session_id=self.session_id,
                speaker=speaker,
                role=role,
                payload=payload,
                audience=audience,
            )
        except Exception:  # noqa: BLE001
            log.exception("could not write to the chat log")

    def history(self, limit: int = DEFAULT_LIMIT, for_dm: bool = False) -> list[dict]:
        """What was said before you arrived, filtered for who is asking.

        The log is handed out on every login, so anything private in it is
        private only until the next person connects. A refusal, a request
        waiting for approval, an expired API key -- each of those went to the DM
        alone at the time and used to be read out to whoever logged in next.
        """
        audiences = (EVERYONE, DM_ONLY) if for_dm else (EVERYONE,)
        try:
            return [
                m.to_dict()
                for m in self.repos.chat.recent(self.campaign_id, limit, audiences)
            ]
        except Exception:  # noqa: BLE001
            log.exception("could not read the chat log")
            return []

    def _campaign_key(self) -> str:
        return campaigns.campaign_key(self.repos)

    # ------------------------------------------------------------------ lifecycle

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.isListening()

    @property
    def port(self) -> int:
        return self._server.serverPort() if self.is_running else 0

    @property
    def members(self) -> list[Member]:
        return [s.member for s in self._sessions.values()]

    def start(self, port: int = DEFAULT_PORT, announce: bool = True) -> bool:
        if self.is_running:
            return True

        server = QWebSocketServer(
            self.session_name, QWebSocketServer.SslMode.NonSecureMode, self
        )
        if not server.listen(QHostAddress.SpecialAddress.Any, port):
            reason = server.errorString() or "could not listen"
            log.error("session server failed to start on port %s: %s", port, reason)
            self.failed.emit(f"Could not host on port {port}: {reason}")
            server.deleteLater()
            return False

        server.newConnection.connect(self._on_new_connection)
        self._server = server
        log.info("hosting %r (campaign %s) on port %s", self.session_name, self.campaign_id, self.port)

        if announce:
            self._beacon.start(self.session_name, self.port)

        self.started.emit(self.port)
        return True

    def stop(self) -> None:
        self._beacon.stop()

        # Detach before closing. QWebSocketServer owns the sockets it handed us,
        # so closing it destroys them -- and a queued `disconnected` would then
        # fire against a dead C++ object.
        for socket in list(self._sessions) + list(self._pending):
            _silence(socket)
            socket.close()

        self._sessions.clear()
        for pending in self._pending.values():
            pending.timer.stop()
        self._pending.clear()

        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
            log.info("session server stopped")
            self.stopped.emit()

    # ---------------------------------------------------------------- connections

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while (socket := self._server.nextPendingConnection()) is not None:
            socket.textMessageReceived.connect(
                lambda text, s=socket: self._on_text(s, text)
            )
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(LOGIN_TIMEOUT_MS)
            timer.timeout.connect(lambda s=socket: self._drop_silent(s))
            timer.start()
            self._pending[socket] = _Pending(timer=timer)

    def _drop_silent(self, socket: QWebSocket) -> None:
        if socket in self._pending:
            log.info("dropping a connection that never logged in")
            self._pending.pop(socket, None)
            socket.close()

    def _on_disconnected(self, socket: QWebSocket) -> None:
        pending = self._pending.pop(socket, None)
        if pending is not None:
            pending.timer.stop()
        session = self._sessions.pop(socket, None)
        if session is not None and session.busy:
            # Otherwise "Autopilot is writing..." outlives the agent.
            self._broadcast(
                MessageType.BUSY_NOW, member=session.member.to_dict(), on=False
            )
        try:
            socket.deleteLater()
        except RuntimeError:
            # The server was closed first and took its sockets with it.
            pass
        if session is not None:
            log.info("%s left", session.member.label)
            self._broadcast_system(f"{session.member.label} left")
            self._send_roster()
            self.roster_changed.emit(self.members)

    # ------------------------------------------------------------------- messages

    def _on_text(self, socket: QWebSocket, text: str) -> None:
        try:
            message = decode(text)
        except ProtocolError as exc:
            self._send(socket, MessageType.ERROR, code="bad_message", message=str(exc))
            return

        pending = self._pending.get(socket)
        if pending is not None:
            if message.type == MessageType.HELLO:
                self._handle_hello(socket, pending, message)
            elif message.type == MessageType.LOGIN:
                self._handle_login(socket, pending, message)
            else:
                self._send(
                    socket, MessageType.ERROR, code="expected_hello", message="log in first"
                )
                socket.close()
            return

        session = self._sessions.get(socket)
        if session is None:
            return  # already gone

        if message.type == MessageType.CHAT:
            self._handle_chat(socket, session, message)
        elif message.type == MessageType.ROLL:
            self._handle_roll(socket, session, message)
        elif message.type == MessageType.EDIT:
            self._handle_edit(socket, session, message)
        elif message.type == MessageType.MOVE:
            self._handle_move(socket, session, message)
        elif message.type == MessageType.TURN:
            self._handle_turn(socket, session, message)
        elif message.type == MessageType.INITIATIVE:
            self._handle_initiative(socket, session, message)
        elif message.type == MessageType.FIGHT:
            self._handle_fight(socket, session, message)
        elif message.type == MessageType.ENLIST:
            self._handle_enlist(socket, session, message)
        elif message.type == MessageType.TERRAIN:
            self._handle_terrain(socket, session, message)
        elif message.type == MessageType.PROPOSE:
            self._handle_propose(socket, session, message)
        elif message.type == MessageType.ACTED:
            self._handle_acted(socket, session, message)
        elif message.type == MessageType.BUSY:
            self._handle_busy(session, message)
        elif message.type == MessageType.TROUBLE:
            self._handle_trouble(socket, session, message)
        elif message.type == MessageType.SPENT:
            self._handle_spent(socket, session, message)
        elif message.type == MessageType.DECIDE:
            self._handle_decision(socket, session, message)
        else:
            log.debug("ignoring unknown message type %r", message.type)

    # ---------------------------------------------------------------------- login

    def _handle_hello(self, socket: QWebSocket, pending: _Pending, message) -> None:
        # The host's own app: it already owns the campaign file.
        token = str(message.get("token", ""))
        if token and secrets.compare_digest(token, self.local_token):
            pending.known = self._trusted_versions(message)
            self._admit(socket, pending, account=None, name=str(message.get("name", "")))
            return

        pending.known = self._trusted_versions(message)

        username = clean_name(message.get("username", ""))
        account = self.repos.accounts.by_username(self.campaign_id, username)

        pending.username = username
        pending.nonce = auth.new_nonce()
        pending.account_id = account.id if account is not None else None

        # An unknown username still gets a challenge, with a salt derived from
        # the name. Otherwise the login screen would answer "does this person
        # play in the campaign?" for anyone who asked.
        salt = account.salt if account is not None else self._decoy_salt(username)

        self._send(
            socket,
            MessageType.CHALLENGE,
            salt=salt.hex(),
            nonce=pending.nonce.hex(),
        )

    def _trusted_versions(self, message) -> dict[int, int]:
        """What the client holds, but only if it is holding *this* campaign.

        A cache from another campaign has the same ids and the same versions, so
        believing it would serve someone a different game's characters.
        """
        if str(message.get("campaign", "")) != self.campaign_key:
            return {}
        return _known_versions(message.get("known"))

    def _decoy_salt(self, username: str) -> bytes:
        import hashlib

        return hashlib.blake2b(
            username.lower().encode("utf-8"),
            key=self.local_token.encode("utf-8")[:64],
            digest_size=auth.SALT_BYTES,
        ).digest()

    def _handle_login(self, socket: QWebSocket, pending: _Pending, message) -> None:
        if not pending.nonce:
            self._send(socket, MessageType.ERROR, code="expected_hello", message="say hello first")
            return

        account = self.repos.accounts.authenticate(
            self.campaign_id, pending.username, pending.nonce, str(message.get("proof", ""))
        )
        if account is None:
            pending.attempts += 1
            log.info("failed login for %r (attempt %d)", pending.username, pending.attempts)
            self._send(
                socket, MessageType.ERROR, code="bad_login", message=auth.explain()
            )
            if pending.attempts >= 3:
                QTimer.singleShot(200, socket.close)
            else:
                # New nonce, so the next attempt cannot replay this one.
                pending.nonce = auth.new_nonce()
                self._send(
                    socket,
                    MessageType.CHALLENGE,
                    salt=(
                        account.salt.hex()
                        if account
                        else self._decoy_salt(pending.username).hex()
                    ),
                    nonce=pending.nonce.hex(),
                )
            return

        self.repos.accounts.touch(account.id)
        self._admit(socket, pending, account=account, name=account.display_name)

    def _admit(self, socket: QWebSocket, pending: _Pending, account, name: str) -> None:
        pending.timer.stop()
        self._pending.pop(socket, None)

        if account is None:  # the host's own app
            viewer = Viewer.dungeon_master()
            member = Member(
                id=new_member_id(),
                name=clean_name(name or "Dungeon Master"),
                role=Role.DM.value,
            )
            account_id = None
        else:
            viewer = Viewer(
                # An agent standing in for the DM answers from the canon, so it
                # sees what they see. What it may *do* is a separate question,
                # settled by the autopilot switch rather than by the projection.
                account_id=account.id,
                is_dm=account.sees_everything,
                owned_entity_ids=self.repos.entities.owned_ids(account.id),
            )
            character = ""
            if account.character_entity_id is not None:
                entity = self.repos.entities.get(account.character_entity_id)
                character = entity.name if entity else ""
            if account.is_agent:
                role = Role.AGENT.value
            elif account.is_dm:
                role = Role.DM.value
            else:
                role = Role.PLAYER.value
            member = Member(
                id=new_member_id(),
                name=clean_name(account.display_name or account.username),
                role=role,
                character=character,
            )
            account_id = account.id

        session = _Session(
            member=member,
            account_id=account_id,
            viewer=viewer,
            is_agent=account is not None and account.is_agent,
        )
        session.visible = visible_entity_ids(self.repos, self.campaign_id, viewer)
        self._sessions[socket] = session
        log.info("%s logged in as %s", member.label, member.role)

        campaign = self.repos.campaigns.get(self.campaign_id)
        self._send(
            socket,
            MessageType.WELCOME,
            you=member.to_dict(),
            session=self.session_name,
            campaign=campaign.name if campaign else "",
            campaign_key=self.campaign_key,
            members=[m.to_dict() for m in self.members],
        )
        # What was said before they arrived, so a session picks up where the
        # last one stopped rather than from an empty panel.
        self._send(
            socket,
            MessageType.HISTORY,
            messages=self.history(for_dm=session.viewer.is_dm),
        )
        self._send_snapshot(socket, session, known=pending.known)
        self._send(socket, MessageType.PANEL_NAMES, names=self.panel_names)
        if session.viewer.is_dm:
            self._send(socket, MessageType.PROPOSALS, proposals=self.proposals)
            self._send(socket, MessageType.FACTS, facts=self.facts)
        # Sent to everyone, filtered per person. Joining halfway through a fight
        # should show you the fight, not an empty grid until something moves.
        self._send(
            socket, MessageType.ENCOUNTER, encounter=self._encounter_for(session)
        )
        # Everyone, not only the agent: a table deserves to know whether it is
        # being answered by a person.
        self._send(socket, MessageType.AUTOPILOT, on=self._autopilot, by=self._autopilot_by)
        self._broadcast_system(f"{member.label} joined", exclude=socket)
        self._send_roster()
        self.roster_changed.emit(self.members)

    # ---------------------------------------------------------------------- state

    def _send_snapshot(
        self, socket: QWebSocket, session: _Session, known: dict[int, int] | None = None
    ) -> None:
        """Everything they may see, or only what has changed since they looked."""
        if known:
            entities, gone = snapshot_since(
                self.repos, self.campaign_id, session.viewer, known
            )
            session.remember_sent(entities)
            # A delta means they still hold everything else at the version they
            # told us about, and we just agreed with them.
            for entity_id, version in known.items():
                session.sent.setdefault(entity_id, version)
            log.info(
                "%s reconnected holding %d entities; sending %d changed, %d gone",
                session.member.label,
                len(known),
                len(entities),
                len(gone),
            )
            self._send(
                socket,
                MessageType.SNAPSHOT,
                entities=entities,
                gone=gone,
                partial=True,
            )
            return

        everything = snapshot(self.repos, self.campaign_id, session.viewer)
        session.remember_sent(everything)
        self._send(
            socket, MessageType.SNAPSHOT, entities=everything, partial=False
        )

    @property
    def panel_names(self) -> dict:
        """What the DM calls each panel, for the rest of the table."""
        prefix = "panel_name.party."
        rows = self.repos.conn.execute(
            "SELECT key, value_json FROM setting WHERE key LIKE ?", (prefix + "%",)
        ).fetchall()
        names = {}
        for row in rows:
            try:
                value = json.loads(row["value_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, str) and value.strip():
                names[row["key"][len(prefix) :]] = value
        return names

    def publish_panel_names(self) -> None:
        """Push the DM's names to everyone connected."""
        self._broadcast(MessageType.PANEL_NAMES, names=self.panel_names)

    def refuse_conflicting(self, entity_id: int) -> int:
        """Refuse proposals made against an older version of this character.

        The DM has since changed the sheet, so the proposal was written against
        something that no longer exists. Approving it would apply a decision
        made about a different character; asking the DM to work out whether it
        still makes sense is worse. Refuse, and let the player ask again.
        """
        entity = self.repos.entities.get(entity_id)
        if entity is None:
            return 0

        refused = 0
        for proposal in self.repos.proposals.open_for_entity(entity_id):
            if proposal.base_version == entity.version:
                continue
            self.repos.proposals.decide(
                proposal.id, "rejected", "the DM changed the sheet in the meantime"
            )
            refused += 1

        if refused:
            self._broadcast_system(
                f"{entity.name} changed, so "
                f"{refused} pending request{'s' if refused > 1 else ''} "
                "no longer applies. Ask again if you still want it."
            )
            self.publish_proposals()
        return refused

    def publish_entity(self, entity_id: int) -> None:
        """Push an entity to everyone allowed to see it, after a DM change.

        Recomputes visibility per connection, so revoking a share arrives as a
        removal rather than leaving a stale copy on a player's screen.
        """
        entity = self.repos.entities.get(entity_id)

        for socket, session in self._sessions.items():
            allowed = visible_entity_ids(self.repos, self.campaign_id, session.viewer)
            was_visible = entity_id in session.visible
            session.visible = allowed

            if entity is None or entity_id not in allowed:
                session.sent.pop(entity_id, None)
                if was_visible:
                    self._send(socket, MessageType.ENTITY_GONE, id=entity_id)
                continue

            projected = project_entity(entity, session.viewer, allowed)
            session.remember_sent(projected)
            self._send(socket, MessageType.ENTITY, entity=projected)

    def publish_all(self) -> None:
        """Resend everything to everyone. Used after a bulk change of shares."""
        for socket, session in self._sessions.items():
            session.visible = visible_entity_ids(self.repos, self.campaign_id, session.viewer)
            self._send_snapshot(socket, session)

    # ------------------------------------------------------------------- actions

    def _handle_chat(self, socket: QWebSocket, session: _Session, message) -> None:
        text = str(message.get("text", "")).strip()[:MAX_CHAT_LENGTH]
        if not text:
            return
        if session.is_agent and not self._autopilot:
            # The entire meaning of autopilot being off. The agent stays
            # connected and keeps receiving, so switching back on is instant --
            # it simply cannot speak, and that is enforced here rather than
            # trusted to it.
            self._send(
                socket,
                MessageType.ERROR,
                code="autopilot_off",
                message="Autopilot is off. The DM is answering.",
            )
            return

        if self._is_an_aside(session):
            self._say_aside(session, text)
            return

        self._record(SAID, text, speaker=session.member.label, role=session.member.role)
        self._broadcast(MessageType.SAID, member=session.member.to_dict(), text=text)

    def _is_an_aside(self, session: _Session) -> bool:
        """Whether this line is direction rather than speech.

        While autopilot is on there is one voice at the table, and it is the
        agent's. A DM typing then is *directing* -- "there is something behind
        the door" -- and if their words also went out the party would hear two
        DMs, one of whom keeps being contradicted by the other.

        So it goes to the back room: the DM, any co-DM, and the agent, which
        answers it as part of the conversation. Wanting to speak to the table
        directly is what the switch is for.
        """
        return (
            self._autopilot
            and session.viewer.is_dm
            and not session.is_agent
        )

    def _say_aside(self, session: _Session, text: str) -> None:
        """A DM's line while autopilot is on. Heard by the agent, not the party."""
        self._record(
            SAID,
            text,
            speaker=session.member.label,
            role=session.member.role,
            audience=DM_ONLY,
        )
        log.info("%s directed autopilot: %s", session.member.label, text)
        frame = encode(
            MessageType.SAID,
            member=session.member.to_dict(),
            text=text,
            # So the DM's own screen can show that it did not go out. A line
            # that looks public and was not is worse than one held back.
            aside=True,
        )
        for socket, other in self._sessions.items():
            if other.viewer.is_dm:
                socket.sendTextMessage(frame)

    def _handle_busy(self, session: _Session, message) -> None:
        """Someone is composing. Told to everyone, because the point of it is
        that a table with nothing on screen assumes nothing is happening."""
        on = bool(message.get("on"))
        if session.busy == on:
            return
        session.busy = on
        self._broadcast(
            MessageType.BUSY_NOW, member=session.member.to_dict(), on=on
        )

    def _handle_trouble(self, socket: QWebSocket, session: _Session, message) -> None:
        """The agent could not answer, and the DM should hear why.

        Told privately rather than announced: a table does not need to watch a
        machine apologise, and the DM is the only one who can do anything about
        an expired key.
        """
        if not session.is_agent:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="Only an agent reports trouble answering.",
            )
            return
        text = str(message.get("message", "")).strip()[:MAX_CHAT_LENGTH]
        if text:
            log.warning("the agent could not answer: %s", text)
            self._tell_dms(f"Autopilot could not answer: {text}")

    def _handle_spent(self, socket: QWebSocket, session: _Session, message) -> None:
        """What the agent has cost so far.

        Only an agent may report it -- a player claiming a spend figure would
        be putting a number on the DM's screen that nothing generated. And only
        DMs are told: it is their bill.
        """
        if not session.is_agent:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="Only an agent reports what it has spent.",
            )
            return

        self._spend = {
            "tokens_in": int(message.get("tokens_in") or 0),
            "tokens_out": int(message.get("tokens_out") or 0),
            "cached": int(message.get("cached") or 0),
            "dollars": float(message.get("dollars") or 0.0),
            "turns": int(message.get("turns") or 0),
            "model": str(message.get("model") or ""),
        }
        self.publish_spend()

    @property
    def spend(self) -> dict:
        return dict(self._spend)

    def publish_spend(self) -> None:
        frame = encode(MessageType.SPEND, **self._spend)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm and not session.is_agent:
                socket.sendTextMessage(frame)

    def _handle_roll(self, socket: QWebSocket, session: _Session, message) -> None:
        notation = str(message.get("notation", "")).strip()[:MAX_NOTATION_LENGTH]
        try:
            result = roll(notation)
        except DiceError as exc:
            self._send(socket, MessageType.ERROR, code="bad_dice", message=str(exc))
            return
        self._record(
            ROLLED,
            result.describe(),
            speaker=session.member.label,
            role=session.member.role,
            payload={"total": result.total, "rolls": result.rolls},
        )
        self._broadcast(
            MessageType.ROLLED,
            member=session.member.to_dict(),
            notation=result.notation,
            rolls=result.rolls,
            kept=result.kept,
            modifier=result.modifier,
            total=result.total,
            description=result.describe(),
        )

    def _handle_edit(self, socket: QWebSocket, session: _Session, message) -> None:
        """A player asking for a change. Nothing here is applied.

        Everything a player sends is a request the DM answers, hit points
        included. The host writes nothing on a player's say-so.
        """
        entity_id = message.get("id")
        changes = message.get("changes")
        if not isinstance(entity_id, int) or not isinstance(changes, dict):
            return

        if not session.viewer.owns(entity_id):
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="You can only ask about your own characters.",
            )
            return

        entity = self.repos.entities.get(entity_id)
        if entity is None:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="That character no longer exists.",
            )
            return

        # The version they are working against is the one we last sent them.
        # Taking it from the message would let a client pick a convenient one,
        # or omit it and slip past the check entirely.
        base = session.sent.get(entity_id, entity.version)
        if base != entity.version:
            log.info(
                "refused a stale request for %s from %s",
                entity_id,
                session.member.label,
            )
            self._send(
                socket,
                MessageType.ERROR,
                code="stale",
                message=(
                    "Your DM changed this character while you were editing it, "
                    "so your request was not sent. Here it is as it stands now."
                ),
            )
            self.publish_entity(entity_id)
            return

        wanted = self._collect_request(entity, changes)
        if not wanted:
            return  # nothing actually differs; not worth asking about

        problem = self._why_not(entity, wanted)
        if problem:
            # Refused outright rather than queued: the DM should not be asked
            # to approve something that is not a legal sheet.
            self._send(socket, MessageType.ERROR, code="illegal", message=problem)
            return

        self._propose(session, entity_id, wanted, base)
        self._send(
            socket,
            MessageType.SYSTEM,
            text=f"Sent to your DM: {describe_changes(wanted)}",
        )

    def _collect_request(self, entity, changes: dict) -> dict:
        """What they are actually asking to change, and nothing else."""
        proposed = (changes.get("data") or {}).get("sheet")
        wanted: dict = {}
        if isinstance(proposed, dict):
            wanted.update(
                changed_sheet_fields(entity.data.get("sheet") or {}, proposed)
            )

        summary = changes.get("summary")
        if isinstance(summary, str) and summary.strip() != entity.summary:
            wanted["summary"] = summary.strip()
        return wanted

    def _why_not(self, entity, wanted: dict) -> str:
        """A reason the request cannot stand, or empty if it is fine.

        Checked here because the client that sent it is the one thing we cannot
        take at its word, and because the DM should not be handed nonsense to
        approve.
        """
        sheet = dict(entity.data.get("sheet") or {})
        if not sheet:
            return ""
        sheet.update({k: v for k, v in wanted.items() if k != "summary"})
        report = validate(sheet, self.content)
        return "" if report.ok else f"That is not a legal sheet: {report.summary()}"


    # -------------------------------------------------------------- the fight

    def _encounter_for(self, session: _Session) -> dict:
        """The running fight as this one person may see it, or ``{}`` for none.

        Only the *running* fight is ever sent. A DM preparing next week's ambush
        in another encounter is doing exactly the thing the party must not see,
        and "which fight is on screen" is not a distinction worth trusting to
        the panel.
        """
        encounter = self.repos.encounters.running(self.campaign_id)
        if encounter is None:
            return {}
        return project_encounter(
            encounter,
            self.repos.encounters.combatants(encounter.id),
            session.viewer,
            visible_entity_ids(self.repos, self.campaign_id, session.viewer),
            self.repos.encounters.obstacles(encounter.id),
        )

    def publish_encounter(self) -> None:
        """Push the fight to everyone, filtered per person.

        Not one frame broadcast: two people at the same table are shown
        different tokens, so there is no shared frame to send.
        """
        for socket, session in self._sessions.items():
            self._send(
                socket, MessageType.ENCOUNTER, encounter=self._encounter_for(session)
            )

    def _may_run_the_fight(self, session: _Session) -> bool:
        """Who may move a token, pass the turn, or set an initiative.

        One question for all three, because they are one authority: whoever is
        running the fight. The DM always is. An agent is gated on autopilot
        exactly as its chat is, and for the same reason -- "off" has to be
        something the host enforces, not something the agent is trusted to
        observe. A player cannot do any of it yet; see the known gaps in
        ARCHITECTURE.md.
        """
        if session.is_agent:
            return self._autopilot
        return session.viewer.is_dm

    def _refuse_the_fight(self, socket: QWebSocket) -> None:
        self._send(
            socket,
            MessageType.ERROR,
            code="refused",
            message="Only whoever is running the fight can do that.",
        )

    def _the_running_fight(self):
        return self.repos.encounters.running(self.campaign_id)

    def _handle_turn(self, socket: QWebSocket, session: _Session, message) -> None:
        """Start the fight, pass the turn, or stop the clock.

        The same three buttons the DM's panel has, reachable over the wire, so
        an agent running a combat is doing the thing the DM does rather than a
        parallel thing that happens to look like it.
        """
        if not self._may_run_the_fight(session):
            self._refuse_the_fight(socket)
            return

        encounter = self._the_running_fight()
        if encounter is None:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="There is no fight being run.",
            )
            return

        action = str(message.get("action", "")).lower()
        if action == "begin":
            self.repos.encounters.begin(encounter.id)
        elif action == "next":
            self.repos.encounters.advance(encounter.id)
        elif action == "end":
            self.repos.encounters.end(encounter.id)
        else:
            self._send(
                socket,
                MessageType.ERROR,
                code="bad_message",
                message="A turn is begin, next or end.",
            )
            return

        log.info("%s: turn %r", session.member.label, action)
        self._announce_turn(action)
        self.publish_encounter()
        self.encounter_applied.emit()

    def _announce_turn(self, action: str) -> None:
        """Say it in the chat as well, and keep it.

        Whose turn it was is part of what happened at that table, and a fight
        run by an agent is exactly the case where somebody will want to read
        back what it did.
        """
        encounter = self._the_running_fight()
        if action == "end":
            self._broadcast_system("The fight is over.")
            return
        if encounter is None:
            return
        combatant = (
            self.repos.encounters.combatant(encounter.turn_combatant_id)
            if encounter.turn_combatant_id
            else None
        )
        entity = (
            self.repos.entities.get(combatant.entity_id)
            if combatant is not None and combatant.entity_id is not None
            else None
        )
        whose = entity.name if entity is not None else "someone"
        if action == "begin":
            self._broadcast_system(f"Roll initiative. {whose} is up first.")
        else:
            self._broadcast_system(f"Round {encounter.round}: {whose} is up.")

    def _handle_fight(self, socket: QWebSocket, session: _Session, message) -> None:
        """Start a fight. The one the DM's New fight button makes, over the wire.

        Deliberately the same repository call: an agent setting up a combat must
        not be able to produce an encounter the app could not have produced
        itself.
        """
        if not self._may_run_the_fight(session):
            self._refuse_the_fight(socket)
            return

        encounter = self.repos.encounters.create(
            self.campaign_id,
            name=str(message.get("name", ""))[:MAX_NAME_LENGTH],
            width=int(message.get("width") or DEFAULT_WIDTH),
            height=int(message.get("height") or DEFAULT_HEIGHT),
        )
        log.info("%s started a fight: %r", session.member.label, encounter.name)
        self.publish_encounter()
        self.encounter_applied.emit()

    def _handle_enlist(self, socket: QWebSocket, session: _Session, message) -> None:
        """Put a creature into the fight being run, optionally on a square."""
        if not self._may_run_the_fight(session):
            self._refuse_the_fight(socket)
            return

        encounter = self._the_running_fight()
        entity_id = message.get("entity")
        if encounter is None or not isinstance(entity_id, int):
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="There is no fight being run.",
            )
            return
        if self.repos.entities.get(entity_id) is None:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="There is no such creature.",
            )
            return

        x, y = message.get("x"), message.get("y")
        initiative = message.get("initiative")
        combatant = self.repos.encounters.add(
            encounter.id,
            entity_id=entity_id,
            initiative=int(initiative) if isinstance(initiative, int) else None,
            x=int(x) if isinstance(x, int) else None,
            y=int(y) if isinstance(y, int) else None,
        )
        if combatant is None:
            # Already in the fight. Not an error worth a message: asking twice
            # is the normal way a model gets to the state it wanted.
            return
        self.publish_encounter()
        self.encounter_applied.emit()

    def _handle_terrain(self, socket: QWebSocket, session: _Session, message) -> None:
        """Put something in the way, or take it out."""
        if not self._may_run_the_fight(session):
            self._refuse_the_fight(socket)
            return

        encounter = self._the_running_fight()
        x, y = message.get("x"), message.get("y")
        if encounter is None or not isinstance(x, int) or not isinstance(y, int):
            return

        wanted = bool(message.get("on", True))
        here = (x, y) in self.repos.encounters.obstacles(encounter.id)
        if here == wanted:
            return
        self.repos.encounters.toggle_obstacle(encounter.id, x, y)
        self.publish_encounter()
        self.encounter_applied.emit()

    def _handle_initiative(self, socket: QWebSocket, session: _Session, message) -> None:
        """Set one combatant's initiative, or clear it with a null."""
        if not self._may_run_the_fight(session):
            self._refuse_the_fight(socket)
            return

        combatant_id = message.get("combatant")
        if not isinstance(combatant_id, int):
            return
        value = message.get("value")
        value = int(value) if isinstance(value, int) else None

        combatant = self.repos.encounters.combatant(combatant_id)
        encounter = self._the_running_fight()
        if combatant is None or encounter is None or combatant.encounter_id != encounter.id:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="That is not in the fight being run.",
            )
            return

        self.repos.encounters.set_initiative(combatant_id, value)
        self.publish_encounter()
        self.encounter_applied.emit()

    def _handle_move(self, socket: QWebSocket, session: _Session, message) -> None:
        """A request to move a token. ``x``/``y`` of null takes it off the map."""
        if not self._may_run_the_fight(session):
            self._refuse_the_fight(socket)
            return

        combatant_id = message.get("combatant")
        if not isinstance(combatant_id, int):
            return
        x = message.get("x")
        y = message.get("y")
        x = int(x) if isinstance(x, int) else None
        y = int(y) if isinstance(y, int) else None

        combatant = self.repos.encounters.combatant(combatant_id)
        encounter = self._the_running_fight()
        if combatant is None or encounter is None or combatant.encounter_id != encounter.id:
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="That is not in the fight being run.",
            )
            return

        if not self.repos.encounters.place(combatant_id, x, y):
            self._send(
                socket,
                MessageType.ERROR,
                code="refused",
                message="That square is off the grid, or someone is standing in it.",
            )
            return

        log.info("%s moved combatant %s to %s,%s", session.member.label, combatant_id, x, y)
        self.publish_encounter()
        # The DM's own map reads the database, so it does not know yet.
        self.encounter_applied.emit()

    # ------------------------------------------------------------ taking a turn
    #
    # A player says "I get behind the orc and hit it with my axe". The agent
    # turns that into squares and a weapon; the host checks it; the player sees
    # exactly what is about to happen and says yes. Three parties, and none of
    # them can skip the others:
    #
    # * The **agent** may only propose. It never moves anybody.
    # * The **host** decides whether the proposal is even legal, and does all
    #   the rolling. The dice were never the client's.
    # * The **player** owns their own turn. Nothing touches their character
    #   until they accept, and refusing costs one click.
    #
    # That last one is why this is not simply the agent moving tokens. Handing
    # a machine the power to walk your character into a fire is a different
    # product from one that offers to.

    def _handle_propose(self, socket: QWebSocket, session: _Session, message) -> None:
        if not self._may_run_the_fight(session):
            self._refuse_the_fight(socket)
            return

        encounter = self._the_running_fight()
        combatant_id = message.get("combatant")
        combatant = (
            self.repos.encounters.combatant(combatant_id)
            if isinstance(combatant_id, int)
            else None
        )
        if encounter is None or combatant is None or combatant.encounter_id != encounter.id:
            self._send_refusal(socket, "That is not in the fight being run.")
            return

        entity = (
            self.repos.entities.get(combatant.entity_id)
            if combatant.entity_id is not None
            else None
        )
        if entity is None:
            self._send_refusal(socket, "That token is not a creature.")
            return
        if entity.owner_account_id is None:
            self._send_refusal(
                socket,
                "Nobody plays that character, so there is nobody to ask. Move it "
                "yourself.",
            )
            return

        problem, action = self._shape_the_action(encounter, combatant, entity, message)
        if problem:
            self._send_refusal(socket, problem)
            return

        # One at a time per character. A second proposal replaces the first
        # rather than queueing: the table has moved on, and a player answering
        # a stale one would act on a map that no longer looks like that.
        self._withdraw_for(combatant.id)
        self._proposed[action["id"]] = action

        log.info("proposed for %s: %s", entity.name, action["text"])
        self._send_to_account(entity.owner_account_id, MessageType.ACTION, **action)
        # The DM watches it happen. They are running the table even while a
        # machine is talking, and a turn being offered is part of that.
        frame = encode(MessageType.ACTION, **action, watching=True)
        for other_socket, other in self._sessions.items():
            if other.viewer.is_dm and other.account_id != entity.owner_account_id:
                other_socket.sendTextMessage(frame)

    def _shape_the_action(self, encounter, combatant, entity, message):
        """Turn a proposal into something legal, or say what is wrong with it.

        Checked here rather than trusted, because the thing that wrote it is a
        language model and the thing that reads it is a person about to press
        Accept.
        """
        move = message.get("move")
        square = None
        if isinstance(move, (list, tuple)) and len(move) == 2:
            x, y = move
            if not isinstance(x, int) or not isinstance(y, int):
                return "That is not a square.", None
            if not encounter.holds(x, y):
                return f"{x},{y} is off the map.", None
            standing = self.repos.encounters.at(encounter.id, x, y)
            if standing is not None and standing.id != combatant.id:
                return f"Somebody is already at {x},{y}.", None
            if (x, y) in self.repos.encounters.obstacles(encounter.id):
                return f"There is something in the way at {x},{y}.", None
            square = [x, y]

        target_id = message.get("target")
        target_name = ""
        if target_id is not None:
            if not isinstance(target_id, int):
                return "That is not a target.", None
            target = self.repos.encounters.combatant(target_id)
            if target is None or target.encounter_id != encounter.id:
                return "That target is not in the fight.", None
            if target.id == combatant.id:
                return "Nobody attacks themselves.", None
            hit = (
                self.repos.entities.get(target.entity_id)
                if target.entity_id is not None
                else None
            )
            target_name = hit.name if hit else "someone"

        return "", {
            "id": secrets.token_hex(6),
            "combatant": combatant.id,
            "who": entity.name,
            "move": square,
            "target": target_id if isinstance(target_id, int) else None,
            "target_name": target_name,
            "weapon": str(message.get("weapon", ""))[:MAX_NAME_LENGTH],
            "text": str(message.get("text", ""))[:MAX_CHAT_LENGTH],
        }

    def _send_refusal(self, socket: QWebSocket, message: str) -> None:
        self._send(socket, MessageType.ERROR, code="refused", message=message)

    def _withdraw_for(self, combatant_id: int) -> None:
        """Take back any turn still waiting for this character."""
        for action_id, action in list(self._proposed.items()):
            if action["combatant"] == combatant_id:
                self._proposed.pop(action_id, None)
                self._broadcast(MessageType.ACTION_GONE, id=action_id)

    def _handle_acted(self, socket: QWebSocket, session: _Session, message) -> None:
        """The player's answer to a turn that was put to them."""
        action = self._proposed.get(str(message.get("id", "")))
        if action is None:
            return  # answered already, or overtaken. Not worth a complaint.

        entity = self._entity_of(action["combatant"])
        # The player whose character it is, or the human DM answering for
        # somebody who has stepped out. Never the agent: it wrote the proposal,
        # and a proposal something can accept on your behalf is not a proposal.
        allowed = entity is not None and (
            session.account_id == entity.owner_account_id
            or (session.viewer.is_dm and not session.is_agent)
        )
        if not allowed:
            self._send_refusal(
                socket,
                "That turn is not yours to answer."
                if not session.is_agent
                else "You proposed it. It is theirs to accept.",
            )
            return

        self._proposed.pop(action["id"], None)
        self._broadcast(MessageType.ACTION_GONE, id=action["id"])

        if not bool(message.get("accept")):
            note = str(message.get("note", "")).strip()[:MAX_CHAT_LENGTH]
            # A refusal with instructions is the player still taking their
            # turn, so it goes to the table as an ordinary line and the agent
            # hears it and offers something else.
            self._record(
                SAID,
                note or f"{action['who']} does something else.",
                speaker=session.member.label,
                role=session.member.role,
            )
            self._broadcast(
                MessageType.SAID,
                member=session.member.to_dict(),
                text=note or f"Not that -- {action['who']} does something else.",
            )
            return

        self._carry_out(action, session)

    def _entity_of(self, combatant_id: int):
        combatant = self.repos.encounters.combatant(combatant_id)
        if combatant is None or combatant.entity_id is None:
            return None
        return self.repos.entities.get(combatant.entity_id)

    def _carry_out(self, action: dict, session: _Session) -> None:
        """Do it. The move first, then the swing, then say what happened."""
        moved = False
        if action.get("move"):
            x, y = action["move"]
            moved = self.repos.encounters.place(action["combatant"], x, y)
            if not moved:
                self._tell(
                    session.account_id,
                    f"{action['who']} could not get to {x},{y} -- somebody or "
                    "something is there now.",
                )

        if moved:
            self._broadcast_system(
                f"{action['who']} moves to {action['move'][0]},{action['move'][1]}."
            )

        if action.get("target") is not None:
            self._swing(action)

        self.publish_encounter()
        self.encounter_applied.emit()

    def _swing(self, action: dict) -> None:
        """One weapon attack, rolled on the host and applied to the target."""
        attacker = self._entity_of(action["combatant"])
        target_combatant = self.repos.encounters.combatant(action["target"])
        target = (
            self.repos.entities.get(target_combatant.entity_id)
            if target_combatant is not None and target_combatant.entity_id is not None
            else None
        )
        if attacker is None or target is None or target_combatant is None:
            return

        sheet = (attacker.data or {}).get("sheet") or {}
        try:
            weapon = attack.find_weapon(sheet, self.content, action.get("weapon", ""))
        except attack.NoAttack as exc:
            self._broadcast_system(f"{attacker.name} cannot attack: {exc}")
            return

        mine = self.repos.encounters.combatant(action["combatant"])
        if mine is not None and mine.on_map and target_combatant.on_map:
            gap = attack.squares_between(
                (mine.x, mine.y), (target_combatant.x, target_combatant.y)
            )
            if not attack.within_reach(weapon, gap):
                self._broadcast_system(
                    f"{target.name} is {gap * 5} feet away -- too far for "
                    f"{weapon.name}."
                )
                return

        result = attack.resolve(
            sheet,
            self.content,
            weapon,
            attack.armour_class(target.data or {}, self.content),
            roll,
        )
        said = result.describe(attacker.name, target.name)
        self._record(ROLLED, said, speaker=attacker.name, role=Role.DM.value)
        self._broadcast(
            MessageType.ROLLED,
            member=Member(id="", name=attacker.name, role=Role.DM.value).to_dict(),
            notation=f"1d20{result.bonus:+d}",
            rolls=[result.roll],
            kept=[result.roll],
            modifier=result.bonus,
            total=result.total,
            description=said,
        )
        if result.hit and result.damage:
            self._take_damage(target, result.damage)

    def _take_damage(self, target, damage: int) -> None:
        """Apply it, and tell everyone who can see the creature.

        Written straight to the entity: this is the host applying its own
        decision, not a client's request. Hit points live in two places on a
        creature -- the shared field and the sheet -- so both move together or
        the DM and the players read different numbers.
        """
        data = dict(target.data or {})
        maximum = data.get("max_hp")
        before = data.get("hp")
        if not isinstance(before, int):
            before = maximum if isinstance(maximum, int) else None
        if not isinstance(before, int):
            return

        after = max(0, before - int(damage))
        data["hp"] = after
        sheet = data.get("sheet")
        if isinstance(sheet, dict):
            sheet["hp_current"] = after
        if after == 0:
            data["status"] = "down"
        target.data = data
        self.repos.entities.update(target)

        self._broadcast_system(
            f"{target.name} is down." if after == 0
            else f"{target.name}: {after}"
                 + (f"/{maximum}" if isinstance(maximum, int) else "")
                 + " hit points."
        )
        self.publish_entity(target.id)
        self.entity_applied.emit(target.id)

    # ---------------------------------------------------------------- proposals

    def _propose(self, session: _Session, entity_id: int, build: dict, version: int) -> None:
        """Queue a change to what a character *is*, and tell the table."""
        proposal = self.repos.proposals.propose(
            self.campaign_id, entity_id, session.account_id, build, version
        )
        entity = self.repos.entities.get(entity_id)
        described = describe_changes(build)
        log.info("%s proposed %s for %s", session.member.label, described, entity_id)

        # Told to the DM, not the table. A player asking for something is
        # between the two of them, and announcing every hit point change to
        # everyone would drown the chat.
        self._tell_dms(
            f"{session.member.label} asks to change "
            f"{entity.name if entity else 'a character'}: {described}"
        )
        self.publish_proposals()
        return proposal

    @property
    def proposals(self) -> list[dict]:
        """Open proposals, described for the DM's list."""
        out = []
        for proposal in self.repos.proposals.open_for(self.campaign_id):
            entity = self.repos.entities.get(proposal.entity_id)
            account = (
                self.repos.accounts.get(proposal.account_id)
                if proposal.account_id
                else None
            )
            out.append(
                {
                    "id": proposal.id,
                    "entity_id": proposal.entity_id,
                    "character": entity.name if entity else "?",
                    "who": account.display_name or account.username if account else "?",
                    "changes": proposal.changes,
                    "description": describe_changes(proposal.changes),
                    "stale": bool(entity and entity.version != proposal.base_version),
                }
            )
        return out

    # ------------------------------------------------------------- autopilot

    @property
    def autopilot(self) -> bool:
        return self._autopilot

    def set_autopilot(self, on: bool, by: str = "") -> None:
        """Hand the table to the agent, or take it back.

        Taking it back is immediate and needs no cooperation from the agent: it
        stays connected, and its next line is refused. There is no drain, no
        handshake and nothing to wait for, because the DM interrupting a machine
        mid-sentence is the point.
        """
        on = bool(on)
        if on == self._autopilot:
            return
        self._autopilot = on
        self._autopilot_by = by if on else ""
        log.info("autopilot %s%s", "on" if on else "off", f" by {by}" if by else "")

        self._broadcast(MessageType.AUTOPILOT, on=on, by=self._autopilot_by)
        # Said out loud in the chat as well, and kept in the log. Who was
        # answering is part of what happened at that table.
        self._broadcast_system(
            "Autopilot on -- an agent is answering for the DM."
            if on
            else "Autopilot off -- the DM is answering again."
        )
        self._record(
            SYSTEM,
            "autopilot on" if on else "autopilot off",
            speaker=by or "the DM",
        )

    @property
    def has_agent(self) -> bool:
        """Whether this campaign has an agent login at all."""
        return any(
            account.is_agent
            for account in self.repos.accounts.list(self.campaign_id)
        )

    @property
    def facts(self) -> list[dict]:
        """The canon log as it goes on the wire. Empty for anyone but a DM."""
        return project_facts(self.repos, self.campaign_id, Viewer.dungeon_master())

    def publish_facts(self) -> None:
        """Tell DM-role connections the canon moved.

        Only they get it, so this walks the sessions rather than broadcasting:
        a broadcast with a DM check inside is one edit away from being a leak.
        """
        frame = encode(MessageType.FACTS, facts=self.facts)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm:
                socket.sendTextMessage(frame)

    def publish_proposals(self) -> None:
        """Only the DM needs the queue; players see the chat line."""
        frame = encode(MessageType.PROPOSALS, proposals=self.proposals)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm:
                socket.sendTextMessage(frame)

    def _tell_dms(self, text: str) -> None:
        """Say something to whoever is running the game.

        Kept in the log, but marked as theirs. It used to be recorded as an
        ordinary line, which meant "Autopilot could not answer: Error code 401"
        was read out to the next player who logged in.
        """
        self._record(SYSTEM, text, audience=DM_ONLY)
        frame = encode(MessageType.SYSTEM, text=text, kind=SystemKind.NOTICE.value)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm:
                socket.sendTextMessage(frame)

    def _tell(self, account_id: int | None, text: str) -> None:
        """Say something to one person rather than the whole table."""
        if account_id is None:
            return
        frame = encode(MessageType.SYSTEM, text=text, kind=SystemKind.NOTICE.value)
        for socket, session in self._sessions.items():
            if session.account_id == account_id:
                socket.sendTextMessage(frame)

    def _send_to_account(self, account_id: int | None, message_type, **payload) -> None:
        """One message to every connection that login has open."""
        if account_id is None:
            return
        frame = encode(message_type, **payload)
        for socket, session in self._sessions.items():
            if session.account_id == account_id:
                socket.sendTextMessage(frame)

    def decide(self, proposal_id: int, approve: bool, note: str = "") -> bool:
        """Apply or refuse a request. Called by the DM's app."""
        proposal = self.repos.proposals.get(proposal_id)
        if proposal is None or not proposal.is_open:
            return False

        if not approve:
            self.repos.proposals.decide(proposal_id, "rejected", note)
            entity = self.repos.entities.get(proposal.entity_id)
            described = describe_changes(proposal.changes)
            said = f"Your DM said no to {described}"
            if entity is not None:
                said += f" for {entity.name}"
            if note:
                said += f" -- {note}"
            # Told privately: a refusal is between the two of them, and the
            # table does not need to watch.
            self._tell(proposal.account_id, said)
            # And told *what is true*, not only that they were wrong. Without
            # this their screen keeps showing the change they asked for, which
            # reads exactly like it was accepted.
            self._send_to_account(
                proposal.account_id,
                MessageType.REFUSED,
                id=proposal.entity_id,
                reason=note,
            )
            self.publish_entity(proposal.entity_id)
            self.publish_proposals()
            return True

        entity = self.repos.entities.get(proposal.entity_id)
        if entity is None:
            self.repos.proposals.decide(proposal_id, "stale", "the character is gone")
            self.publish_proposals()
            return False

        changes = dict(proposal.changes)
        if "summary" in changes:
            entity.summary = str(changes.pop("summary"))
        if changes:
            sheet = dict(entity.data.get("sheet") or {})
            sheet.update(changes)
            entity.data["sheet"] = sheet
        self.repos.entities.update(entity)

        self.repos.proposals.decide(proposal_id, "approved")
        self._broadcast_system(
            f"{entity.name}: {describe_changes(proposal.changes)} approved"
        )
        self.publish_entity(proposal.entity_id)
        self.publish_proposals()
        self.entity_applied.emit(proposal.entity_id)
        return True

    def _handle_decision(self, socket: QWebSocket, session: _Session, message) -> None:
        if not session.viewer.is_dm:
            self._send(
                socket, MessageType.ERROR, code="refused",
                message="only the DM decides these",
            )
            return
        proposal_id = message.get("proposal")
        if isinstance(proposal_id, int):
            self.decide(
                proposal_id,
                bool(message.get("approve")),
                str(message.get("note", ""))[:200],
            )

    # --------------------------------------------------------------------- output

    @staticmethod
    def _send(socket: QWebSocket, message_type, **payload) -> None:
        socket.sendTextMessage(encode(message_type, **payload))

    def _broadcast(self, message_type, exclude: QWebSocket | None = None, **payload) -> None:
        frame = encode(message_type, **payload)
        for socket in self._sessions:
            if socket is not exclude:
                socket.sendTextMessage(frame)

    def _broadcast_system(self, text: str, exclude: QWebSocket | None = None) -> None:
        """Housekeeping the whole table hears: joins, leaves, autopilot.

        Marked as chatter so a reader can hide it. Anything a person actually
        needs to act on goes through _tell or _tell_dms instead.
        """
        self._record(SYSTEM, text)
        self._broadcast(
            MessageType.SYSTEM,
            exclude=exclude,
            text=text,
            kind=SystemKind.CHATTER.value,
        )

    def _send_roster(self) -> None:
        self._broadcast(MessageType.ROSTER, members=[m.to_dict() for m in self.members])


def _known_versions(raw) -> dict[int, int]:
    """What the client says it holds, taken as a hint and never trusted.

    A wrong version costs at most a resend, so the only real risk is a client
    sending something enormous; the size is capped for that reason alone.
    """
    if not isinstance(raw, dict):
        return {}
    known: dict[int, int] = {}
    for key, value in list(raw.items())[:5000]:
        try:
            known[int(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return known


def describe_changes(changes: dict) -> str:
    """'level 5 to 6' rather than a dump of JSON."""
    parts = []
    for key, value in sorted(changes.items()):
        caption = key.replace("_index", "").replace("_", " ")
        if isinstance(value, dict):
            parts.append(caption)
        else:
            parts.append(f"{caption} to {value}")
    return ", ".join(parts) or "something"


def _silence(socket: QWebSocket) -> None:
    """Drop our signal connections to a socket that is about to go away."""
    for signal in (socket.textMessageReceived, socket.disconnected):
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            # Already disconnected, or the C++ object is gone. Either is fine.
            pass
