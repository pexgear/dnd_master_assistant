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
from canon_keeper.repo.chat import DEFAULT_LIMIT, ROLLED, SAID, SYSTEM
from canon_keeper_protocol.dice import DiceError, roll
from canon_keeper.repo.entities import StaleWrite
from canon_keeper.rules.validation import validate
from canon_keeper.net.projection import (
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
from canon_keeper_protocol.messages import (
    MAX_CHAT_LENGTH,
    MAX_NOTATION_LENGTH,
    Member,
    MessageType,
    ProtocolError,
    Role,
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
        self._pending: dict[QWebSocket, _Pending] = {}
        self._beacon = discovery.Beacon(self)

    def _open_session(self) -> int | None:
        try:
            return self.repos.sessions.ensure_open(self.campaign_id).id
        except Exception:  # noqa: BLE001 - a log is not worth failing to host
            log.exception("could not open a session for the chat log")
            return None

    def _record(self, kind: str, text: str, speaker: str = "", role: str = "",
                payload: dict | None = None) -> None:
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
            )
        except Exception:  # noqa: BLE001
            log.exception("could not write to the chat log")

    def history(self, limit: int = DEFAULT_LIMIT) -> list[dict]:
        try:
            return [m.to_dict() for m in self.repos.chat.recent(self.campaign_id, limit)]
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
        self._send(socket, MessageType.HISTORY, messages=self.history())
        self._send_snapshot(socket, session, known=pending.known)
        self._send(socket, MessageType.PANEL_NAMES, names=self.panel_names)
        if session.viewer.is_dm:
            self._send(socket, MessageType.PROPOSALS, proposals=self.proposals)
            self._send(socket, MessageType.FACTS, facts=self.facts)
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
        self._record(SAID, text, speaker=session.member.label, role=session.member.role)
        self._broadcast(MessageType.SAID, member=session.member.to_dict(), text=text)

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
        """Say something to whoever is running the game."""
        self._record(SYSTEM, text)
        frame = encode(MessageType.SYSTEM, text=text)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm:
                socket.sendTextMessage(frame)

    def _tell(self, account_id: int | None, text: str) -> None:
        """Say something to one person rather than the whole table."""
        if account_id is None:
            return
        frame = encode(MessageType.SYSTEM, text=text)
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
        self._record(SYSTEM, text)
        self._broadcast(MessageType.SYSTEM, exclude=exclude, text=text)

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
