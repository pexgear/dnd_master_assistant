"""Finding sessions on the local network.

A UDP broadcast beacon rather than mDNS/Bonjour: Qt has no mDNS of its own, the
Python options each drag in a dependency and a service daemon, and none of that
earns its keep for "five people in one room on one subnet".

The host shouts a small JSON packet twice a second; clients listen for a few
seconds and show what they heard. The join code is deliberately **not** in the
packet -- discovery tells you a session exists, it does not let you into it.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket

log = logging.getLogger("canonkeeper.net.discovery")

DISCOVERY_PORT = 8766
BEACON_INTERVAL_MS = 500
MAGIC = "canonkeeper-session"

#: A session not heard from in this long has gone.
STALE_AFTER = 4.0


@dataclass(slots=True)
class FoundSession:
    name: str
    host: str
    port: int
    last_seen: float

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"


class Beacon(QObject):
    """Announces a running session to the local subnet."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._socket: QUdpSocket | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(BEACON_INTERVAL_MS)
        self._timer.timeout.connect(self._announce)
        self._payload = b""

    def start(self, name: str, port: int) -> None:
        self._payload = json.dumps(
            {"magic": MAGIC, "name": name, "port": port},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if self._socket is None:
            self._socket = QUdpSocket(self)
        self._announce()
        self._timer.start()
        log.info("announcing %r on udp/%s", name, DISCOVERY_PORT)

    def stop(self) -> None:
        self._timer.stop()
        if self._socket is not None:
            self._socket.close()
            self._socket.deleteLater()
            self._socket = None

    def _announce(self) -> None:
        if self._socket is None:
            return
        self._socket.writeDatagram(
            self._payload, QHostAddress.SpecialAddress.Broadcast, DISCOVERY_PORT
        )


class Listener(QObject):
    """Collects beacons. Emits the current list whenever it changes."""

    sessions_changed = Signal(list)  # list[FoundSession]

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._socket: QUdpSocket | None = None
        self._found: dict[tuple[str, int], FoundSession] = {}
        self._sweeper = QTimer(self)
        self._sweeper.setInterval(2000)
        self._sweeper.timeout.connect(self._expire)

    def start(self) -> bool:
        if self._socket is not None:
            return True
        socket = QUdpSocket(self)
        # ShareAddress lets a host also listen, so the DM's own app can show the
        # session it is hosting instead of mysteriously missing it.
        if not socket.bind(
            QHostAddress.SpecialAddress.AnyIPv4,
            DISCOVERY_PORT,
            QUdpSocket.BindFlag.ShareAddress | QUdpSocket.BindFlag.ReuseAddressHint,
        ):
            log.warning("could not listen for sessions: %s", socket.errorString())
            socket.deleteLater()
            return False
        socket.readyRead.connect(self._read)
        self._socket = socket
        self._sweeper.start()
        return True

    def stop(self) -> None:
        self._sweeper.stop()
        if self._socket is not None:
            self._socket.close()
            self._socket.deleteLater()
            self._socket = None
        self._found.clear()

    @property
    def sessions(self) -> list[FoundSession]:
        return sorted(self._found.values(), key=lambda s: s.name.lower())

    def _read(self) -> None:
        if self._socket is None:
            return
        changed = False
        while self._socket.hasPendingDatagrams():
            datagram = self._socket.receiveDatagram()
            changed |= self._absorb(
                bytes(datagram.data()), datagram.senderAddress().toString()
            )
        if changed:
            self.sessions_changed.emit(self.sessions)

    def _absorb(self, raw: bytes, sender: str) -> bool:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(parsed, dict) or parsed.get("magic") != MAGIC:
            return False

        port = parsed.get("port")
        if not isinstance(port, int) or not (0 < port < 65536):
            return False

        # IPv4-mapped IPv6 senders arrive as "::ffff:192.168.1.20".
        host = sender.rsplit(":", 1)[-1] if sender.startswith("::ffff:") else sender
        name = str(parsed.get("name", "Session"))[:64]

        key = (host, port)
        is_new = key not in self._found
        self._found[key] = FoundSession(name, host, port, time.monotonic())
        return is_new

    def _expire(self) -> None:
        now = time.monotonic()
        stale = [k for k, s in self._found.items() if now - s.last_seen > STALE_AFTER]
        for key in stale:
            del self._found[key]
        if stale:
            self.sessions_changed.emit(self.sessions)
