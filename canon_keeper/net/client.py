"""The session client.

The DM's app uses this too, pointed at its own loopback server. One path through
the code means the host cannot accidentally see something a player would not.

Reconnects on its own with a backoff, because a laptop lid closing mid-session is
the normal case, not the exceptional one.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

from canon_keeper.net.protocol import (
    Member,
    MessageType,
    ProtocolError,
    Role,
    decode,
    encode,
)

log = logging.getLogger("canonkeeper.net.client")

_BACKOFF_MS = (1000, 2000, 4000, 8000, 15000)


class SessionClient(QObject):
    connected = Signal()
    disconnected = Signal()
    failed = Signal(str)  # human-readable, safe to show
    welcomed = Signal(object)  # Member (you)
    roster_changed = Signal(list)  # list[Member]
    said = Signal(object, str)  # (Member, text)
    rolled = Signal(object, dict)  # (Member, roll payload)
    system = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._socket = QWebSocket()
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.textMessageReceived.connect(self._on_text)
        if hasattr(self._socket, "errorOccurred"):
            self._socket.errorOccurred.connect(self._on_error)

        self._url = ""
        self._code = ""
        self._name = ""
        self._role = Role.PLAYER.value
        self._me: Member | None = None
        self._members: list[Member] = []

        self._wanted = False  # whether the user wants to be connected
        self._attempt = 0
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._reconnect)

    # ------------------------------------------------------------------ lifecycle

    @property
    def is_connected(self) -> bool:
        return self._socket.state().name == "ConnectedState"

    @property
    def me(self) -> Member | None:
        return self._me

    @property
    def members(self) -> list[Member]:
        return list(self._members)

    def join(self, url: str, code: str, name: str, role: str = Role.PLAYER.value) -> None:
        self.leave()
        self._url, self._code, self._name, self._role = url, code, name, role
        self._wanted = True
        self._attempt = 0
        self._open()

    def leave(self) -> None:
        self._wanted = False
        self._retry.stop()
        self._me = None
        self._members = []
        if self._socket.state().name != "UnconnectedState":
            self._socket.close()

    def _open(self) -> None:
        log.info("connecting to %s", self._url)
        self._socket.open(QUrl(self._url))

    def _reconnect(self) -> None:
        if self._wanted:
            self._open()

    # ------------------------------------------------------------------- sending

    def send_chat(self, text: str) -> bool:
        return self._send(MessageType.CHAT, text=text)

    def send_roll(self, notation: str) -> bool:
        return self._send(MessageType.ROLL, notation=notation)

    def _send(self, message_type, **payload) -> bool:
        if not self.is_connected:
            self.failed.emit("Not connected.")
            return False
        self._socket.sendTextMessage(encode(message_type, **payload))
        return True

    # ------------------------------------------------------------------- signals

    def _on_connected(self) -> None:
        self._attempt = 0
        self._socket.sendTextMessage(
            encode(MessageType.HELLO, code=self._code, name=self._name, role=self._role)
        )

    def _on_disconnected(self) -> None:
        was_in = self._me is not None
        self._me = None
        self._members = []
        self.disconnected.emit()
        if was_in:
            self.roster_changed.emit([])
        if self._wanted:
            delay = _BACKOFF_MS[min(self._attempt, len(_BACKOFF_MS) - 1)]
            self._attempt += 1
            log.info("reconnecting in %sms", delay)
            self._retry.start(delay)

    def _on_error(self, _error) -> None:
        message = self._socket.errorString() or "connection failed"
        # Only surface the first failure of a run; a reconnect loop that shouts
        # every few seconds is worse than silence.
        if self._attempt <= 1:
            self.failed.emit(message)

    def _on_text(self, raw: str) -> None:
        try:
            message = decode(raw)
        except ProtocolError as exc:
            log.warning("unreadable frame from server: %s", exc)
            return

        if message.type == MessageType.WELCOME:
            self._me = Member.from_dict(message.get("you", {}))
            self._members = [Member.from_dict(m) for m in message.get("members", [])]
            self.welcomed.emit(self._me)
            self.roster_changed.emit(self.members)
            self.connected.emit()

        elif message.type == MessageType.ROSTER:
            self._members = [Member.from_dict(m) for m in message.get("members", [])]
            self.roster_changed.emit(self.members)

        elif message.type == MessageType.SAID:
            self.said.emit(
                Member.from_dict(message.get("member", {})), str(message.get("text", ""))
            )

        elif message.type == MessageType.ROLLED:
            self.rolled.emit(Member.from_dict(message.get("member", {})), message.payload)

        elif message.type == MessageType.SYSTEM:
            self.system.emit(str(message.get("text", "")))

        elif message.type == MessageType.ERROR:
            text = str(message.get("message", "The host refused the connection."))
            if message.get("code") == "bad_code":
                # A wrong code will never succeed on retry, so stop rather than
                # hammering the host every second.
                self._wanted = False
                self._retry.stop()
            self.failed.emit(text)
