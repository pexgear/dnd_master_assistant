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

        self._server: QWebSocketServer | None = None
        self._sessions: dict[QWebSocket, _Session] = {}
        self._pending: dict[QWebSocket, _Pending] = {}
        self._beacon = discovery.Beacon(self)

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
        else:
            log.debug("ignoring unknown message type %r", message.type)

    # ---------------------------------------------------------------------- login

    def _handle_hello(self, socket: QWebSocket, pending: _Pending, message) -> None:
        # The host's own app: it already owns the campaign file.
        token = str(message.get("token", ""))
        if token and secrets.compare_digest(token, self.local_token):
            self._admit(socket, pending, account=None, name=str(message.get("name", "")))
            return

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
            members=[m.to_dict() for m in self.members],
        )
        self._send_snapshot(socket, session)
        self._send(socket, MessageType.PANEL_NAMES, names=self.panel_names)
        self._broadcast_system(f"{member.label} joined", exclude=socket)
        self._send_roster()
        self.roster_changed.emit(self.members)

    # ---------------------------------------------------------------------- state

    def _send_snapshot(self, socket: QWebSocket, session: _Session) -> None:
        self._send(
            socket,
            MessageType.SNAPSHOT,
            entities=snapshot(self.repos, self.campaign_id, session.viewer),
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


def _silence(socket: QWebSocket) -> None:
    """Drop our signal connections to a socket that is about to go away."""
    for signal in (socket.textMessageReceived, socket.disconnected):
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            # Already disconnected, or the C++ object is gone. Either is fine.
            pass
