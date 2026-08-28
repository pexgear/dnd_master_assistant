"""The session host.

Runs inside the DM's app or inside the headless ``canonkeeper-server``; the class
does not know or care which. It owns the roster, rolls the dice, and rebroadcasts
chat.

Clients are untrusted. A connection that does not say hello with the right join
code within a few seconds is dropped, names and message lengths are capped by the
protocol module, and dice are rolled here rather than accepted from the wire.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from canon_keeper.net import discovery
from canon_keeper.net.dice import DiceError, roll
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
    new_join_code,
    new_member_id,
    normalise_code,
)

log = logging.getLogger("canonkeeper.net.server")

DEFAULT_PORT = 8765

#: A socket that has not identified itself by then is closed. Stops a port
#: scanner or a stalled client from occupying a slot indefinitely.
HELLO_TIMEOUT_MS = 5000


def _silence(socket: QWebSocket) -> None:
    """Drop our signal connections to a socket that is about to go away."""
    for signal in (socket.textMessageReceived, socket.disconnected):
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            # Already disconnected, or the C++ object is gone. Either is fine.
            pass


class SessionServer(QObject):
    started = Signal(int)  # port
    stopped = Signal()
    failed = Signal(str)
    roster_changed = Signal(list)  # list[Member]

    def __init__(
        self,
        session_name: str = "Canon Keeper session",
        code: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.session_name = session_name
        self.code = code or new_join_code()

        self._server: QWebSocketServer | None = None
        self._members: dict[QWebSocket, Member] = {}
        self._pending: dict[QWebSocket, QTimer] = {}
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
        return list(self._members.values())

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
        log.info("hosting %r on port %s (code %s)", self.session_name, self.port, self.code)

        if announce:
            self._beacon.start(self.session_name, self.port)

        self.started.emit(self.port)
        return True

    def stop(self) -> None:
        self._beacon.stop()

        # Detach before closing. QWebSocketServer owns the sockets it handed us,
        # so closing it destroys them -- and a queued `disconnected` would then
        # fire against a dead C++ object.
        for socket in list(self._members) + list(self._pending):
            _silence(socket)
            socket.close()

        self._members.clear()
        for timer in self._pending.values():
            timer.stop()
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
            timer.setInterval(HELLO_TIMEOUT_MS)
            timer.timeout.connect(lambda s=socket: self._drop_silent(s))
            timer.start()
            self._pending[socket] = timer

    def _drop_silent(self, socket: QWebSocket) -> None:
        if socket in self._pending:
            log.info("dropping a connection that never said hello")
            self._pending.pop(socket, None)
            socket.close()

    def _on_disconnected(self, socket: QWebSocket) -> None:
        timer = self._pending.pop(socket, None)
        if timer is not None:
            timer.stop()
        member = self._members.pop(socket, None)
        try:
            socket.deleteLater()
        except RuntimeError:
            # The server was closed first and took its sockets with it.
            pass
        if member is not None:
            log.info("%s left", member.name)
            self._broadcast_system(f"{member.name} left")
            self._send_roster()
            self.roster_changed.emit(self.members)

    # ------------------------------------------------------------------- messages

    def _on_text(self, socket: QWebSocket, text: str) -> None:
        try:
            message = decode(text)
        except ProtocolError as exc:
            self._send(socket, MessageType.ERROR, code="bad_message", message=str(exc))
            return

        if socket in self._pending:
            self._handle_hello(socket, message)
            return

        member = self._members.get(socket)
        if member is None:
            return  # already gone

        if message.type == MessageType.CHAT:
            self._handle_chat(member, message)
        elif message.type == MessageType.ROLL:
            self._handle_roll(socket, member, message)
        else:
            log.debug("ignoring unknown message type %r", message.type)

    def _handle_hello(self, socket: QWebSocket, message) -> None:
        if message.type != MessageType.HELLO:
            self._send(socket, MessageType.ERROR, code="expected_hello", message="say hello first")
            socket.close()
            return

        if normalise_code(message.get("code")) != self.code:
            log.info("rejected a join with the wrong code")
            self._send(
                socket, MessageType.ERROR, code="bad_code", message="That join code is wrong."
            )
            # Let the frame flush before the socket goes away, or the client
            # sees a bare disconnect and cannot tell the user why.
            QTimer.singleShot(100, socket.close)
            return

        timer = self._pending.pop(socket, None)
        if timer is not None:
            timer.stop()

        requested = message.get("role")
        role = Role.DM.value if requested == Role.DM.value else Role.PLAYER.value
        member = Member(
            id=new_member_id(), name=clean_name(message.get("name")), role=role
        )
        self._members[socket] = member
        log.info("%s joined as %s", member.name, member.role)

        self._send(
            socket,
            MessageType.WELCOME,
            you=member.to_dict(),
            session=self.session_name,
            members=[m.to_dict() for m in self.members],
        )
        self._broadcast_system(f"{member.name} joined", exclude=socket)
        self._send_roster()
        self.roster_changed.emit(self.members)

    def _handle_chat(self, member: Member, message) -> None:
        text = str(message.get("text", "")).strip()[:MAX_CHAT_LENGTH]
        if not text:
            return
        self._broadcast(MessageType.SAID, member=member.to_dict(), text=text)

    def _handle_roll(self, socket: QWebSocket, member: Member, message) -> None:
        notation = str(message.get("notation", "")).strip()[:MAX_NOTATION_LENGTH]
        try:
            result = roll(notation)
        except DiceError as exc:
            self._send(socket, MessageType.ERROR, code="bad_dice", message=str(exc))
            return
        self._broadcast(
            MessageType.ROLLED,
            member=member.to_dict(),
            notation=result.notation,
            rolls=result.rolls,
            kept=result.kept,
            modifier=result.modifier,
            total=result.total,
            description=result.describe(),
        )

    # --------------------------------------------------------------------- output

    @staticmethod
    def _send(socket: QWebSocket, message_type, **payload) -> None:
        socket.sendTextMessage(encode(message_type, **payload))

    def _broadcast(self, message_type, exclude: QWebSocket | None = None, **payload) -> None:
        frame = encode(message_type, **payload)
        for socket in self._members:
            if socket is not exclude:
                socket.sendTextMessage(frame)

    def _broadcast_system(self, text: str, exclude: QWebSocket | None = None) -> None:
        self._broadcast(MessageType.SYSTEM, exclude=exclude, text=text)

    def _send_roster(self) -> None:
        self._broadcast(MessageType.ROSTER, members=[m.to_dict() for m in self.members])
