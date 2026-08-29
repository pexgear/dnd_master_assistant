"""The session host, bound to one campaign.

Runs inside the DM's app or inside the headless ``canonkeeper-server``; the class
does not know or care which. It owns the roster, rolls the dice, rebroadcasts
chat, and -- the part that matters -- decides what each logged-in player is
allowed to see.

Clients are untrusted. Every outbound entity goes through
:mod:`canon_keeper.net.projection` first, so a player's app is never sent a
secret and asked to hide it.

Logging in is a challenge/response: the password never crosses the wire. See
:mod:`canon_keeper.net.auth`.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from canon_keeper.net import auth, discovery
from canon_keeper.net.dice import DiceError, roll
from canon_keeper.repo.entities import StaleWrite
from canon_keeper.net.projection import (
    EditRefused,
    snapshot_since,
    split_sheet_change,
    Viewer,
    apply_player_edit,
    project_entity,
    snapshot,
    visible_entity_ids,
)
from canon_keeper.net.protocol import (
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
    visible: set[int] = field(default_factory=set)


class SessionServer(QObject):
    started = Signal(int)  # port
    stopped = Signal()
    failed = Signal(str)
    roster_changed = Signal(list)  # list[Member]

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

        self._server: QWebSocketServer | None = None
        self._sessions: dict[QWebSocket, _Session] = {}
        self._pending: dict[QWebSocket, _Pending] = {}
        self._beacon = discovery.Beacon(self)

    def _campaign_key(self) -> str:
        key = self.repos.settings.get("campaign_key", "")
        if not isinstance(key, str) or not key:
            key = secrets.token_hex(16)
            self.repos.settings.set("campaign_key", key)
        return key

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
            self._handle_chat(session, message)
        elif message.type == MessageType.ROLL:
            self._handle_roll(socket, session, message)
        elif message.type == MessageType.EDIT:
            self._handle_edit(socket, session, message)
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
                account_id=account.id,
                is_dm=account.is_dm,
                owned_entity_ids=self.repos.entities.owned_ids(account.id),
            )
            character = ""
            if account.character_entity_id is not None:
                entity = self.repos.entities.get(account.character_entity_id)
                character = entity.name if entity else ""
            member = Member(
                id=new_member_id(),
                name=clean_name(account.display_name or account.username),
                role=Role.DM.value if account.is_dm else Role.PLAYER.value,
                character=character,
            )
            account_id = account.id

        session = _Session(member=member, account_id=account_id, viewer=viewer)
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
        self._send_snapshot(socket, session, known=pending.known)
        self._send(socket, MessageType.PANEL_NAMES, names=self.panel_names)
        if session.viewer.is_dm:
            self._send(socket, MessageType.PROPOSALS, proposals=self.proposals)
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

        self._send(
            socket,
            MessageType.SNAPSHOT,
            entities=snapshot(self.repos, self.campaign_id, session.viewer),
            partial=False,
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
                if was_visible:
                    self._send(socket, MessageType.ENTITY_GONE, id=entity_id)
                continue

            self._send(
                socket,
                MessageType.ENTITY,
                entity=project_entity(entity, session.viewer, allowed),
            )

    def publish_all(self) -> None:
        """Resend everything to everyone. Used after a bulk change of shares."""
        for socket, session in self._sessions.items():
            session.visible = visible_entity_ids(self.repos, self.campaign_id, session.viewer)
            self._send_snapshot(socket, session)

    # ------------------------------------------------------------------- actions

    def _handle_chat(self, session: _Session, message) -> None:
        text = str(message.get("text", "")).strip()[:MAX_CHAT_LENGTH]
        if not text:
            return
        self._broadcast(MessageType.SAID, member=session.member.to_dict(), text=text)

    def _handle_roll(self, socket: QWebSocket, session: _Session, message) -> None:
        notation = str(message.get("notation", "")).strip()[:MAX_NOTATION_LENGTH]
        try:
            result = roll(notation)
        except DiceError as exc:
            self._send(socket, MessageType.ERROR, code="bad_dice", message=str(exc))
            return
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
        entity_id = message.get("id")
        changes = message.get("changes")
        if not isinstance(entity_id, int) or not isinstance(changes, dict):
            return

        # Split the sheet before applying anything: hit points go through now,
        # a change of level goes to the DM.
        proposed = (changes.get("data") or {}).get("sheet")
        if isinstance(proposed, dict) and session.viewer.owns(entity_id):
            entity = self.repos.entities.get(entity_id)
            existing = (entity.data.get("sheet") or {}) if entity else {}
            state, build = split_sheet_change(existing, proposed)
            changes = {**changes, "data": {**(changes.get("data") or {}), "sheet": state}}
            if build:
                self._propose(session, entity_id, build, entity.version if entity else 0)
        expected = message.get("version")
        try:
            apply_player_edit(
                self.repos,
                session.viewer,
                entity_id,
                changes,
                expected_version=expected if isinstance(expected, int) else None,
            )
        except EditRefused as exc:
            self._send(socket, MessageType.ERROR, code="refused", message=str(exc))
            return
        except StaleWrite as exc:
            # Someone else moved it underneath them. Refuse, and resend so their
            # screen catches up rather than showing a change that did not happen.
            self._send(socket, MessageType.ERROR, code="stale", message=str(exc))
            self.publish_entity(entity_id)
            return
        self.publish_entity(entity_id)

    # ---------------------------------------------------------------- proposals

    def _propose(self, session: _Session, entity_id: int, build: dict, version: int) -> None:
        """Queue a change to what a character *is*, and tell the table."""
        proposal = self.repos.proposals.propose(
            self.campaign_id, entity_id, session.account_id, build, version
        )
        entity = self.repos.entities.get(entity_id)
        described = describe_changes(build)
        log.info("%s proposed %s for %s", session.member.label, described, entity_id)

        self._broadcast_system(
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

    def publish_proposals(self) -> None:
        """Only the DM needs the queue; players see the chat line."""
        frame = encode(MessageType.PROPOSALS, proposals=self.proposals)
        for socket, session in self._sessions.items():
            if session.viewer.is_dm:
                socket.sendTextMessage(frame)

    def decide(self, proposal_id: int, approve: bool) -> bool:
        """Apply or discard a proposal. Called by the DM's app."""
        proposal = self.repos.proposals.get(proposal_id)
        if proposal is None or not proposal.is_open:
            return False

        if not approve:
            self.repos.proposals.decide(proposal_id, "rejected")
            self.publish_proposals()
            return True

        entity = self.repos.entities.get(proposal.entity_id)
        if entity is None:
            self.repos.proposals.decide(proposal_id, "stale", "the character is gone")
            self.publish_proposals()
            return False

        sheet = dict(entity.data.get("sheet") or {})
        sheet.update(proposal.changes)
        entity.data["sheet"] = sheet
        self.repos.entities.update(entity)

        self.repos.proposals.decide(proposal_id, "approved")
        self._broadcast_system(
            f"{entity.name}: {describe_changes(proposal.changes)} approved"
        )
        self.publish_entity(proposal.entity_id)
        self.publish_proposals()
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
            self.decide(proposal_id, bool(message.get("approve")))

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
