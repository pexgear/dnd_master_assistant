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

from canon_keeper.net import cache
from canon_keeper_protocol import auth
from canon_keeper_protocol.messages import (
    MAX_HOST_FRAME_BYTES,
    Member,
    MessageType,
    ProtocolError,
    Role,
    decode,
    encode,
)
from canon_keeper.net.state import SharedState

log = logging.getLogger("canonkeeper.net.client")

_BACKOFF_MS = (1000, 2000, 4000, 8000, 15000)

#: QWebSocket has no connect timeout of its own, so an unreachable host falls
#: back to the OS TCP timeout -- around 40 seconds on Windows, during which the
#: app looks like it has simply hung. A firewall silently dropping the SYN is
#: the common cause, and that deserves a fast, specific answer.
CONNECT_TIMEOUT_MS = 8000


class SessionClient(QObject):
    connected = Signal()
    disconnected = Signal()
    failed = Signal(str)  # human-readable, safe to show
    welcomed = Signal(object)  # Member (you)
    roster_changed = Signal(list)  # list[Member]
    said = Signal(object, str)  # (Member, text)
    rolled = Signal(object, dict)  # (Member, roll payload)
    system = Signal(str)
    #: Emitted once the host's filtered view of the campaign has arrived.
    state_replaced = Signal()
    #: What the DM calls each panel, as a {panel_id: name} mapping.
    panel_names_received = Signal(dict)
    #: Build changes waiting on the DM. Only the DM's client sees these.
    proposals_received = Signal(list)
    #: The canon log. Only ever arrives on a DM-role connection; the host
    #: sends a player nothing at all rather than a redacted version.
    facts_received = Signal(list)
    #: What was said before we arrived, oldest first.
    history_received = Signal(list)

    def __init__(self, parent: QObject | None = None, state: SharedState | None = None) -> None:
        super().__init__(parent)
        self._socket = QWebSocket()
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.textMessageReceived.connect(self._on_text)
        if hasattr(self._socket, "errorOccurred"):
            self._socket.errorOccurred.connect(self._on_error)

        self._url = ""
        self._username = ""
        self._password = ""
        self._token = ""
        self._name = ""
        self._role = Role.PLAYER.value
        self._me: Member | None = None
        self._members: list[Member] = []
        #: The host's filtered view of the campaign, for player mode. Shared
        #: with the panels via AppContext, so they need no socket of their own.
        self.state = state if state is not None else SharedState(self)

        #: Where this session's cache lives, once we know who we are talking to.
        self._cache_key: tuple[str, str] | None = None
        #: Which campaign the cache belongs to. Sent with the versions we
        #: hold, so the host can tell whether to believe them.
        self._campaign_key = ""
        self._wanted = False  # whether the user wants to be connected
        self._attempt = 0
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._reconnect)

        self._connect_timeout = QTimer(self)
        self._connect_timeout.setSingleShot(True)
        self._connect_timeout.timeout.connect(self._on_connect_timeout)

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

    def join(self, url: str, username: str, password: str, name: str = "") -> None:
        """Log in with an account. The password never leaves this process."""
        self.leave()
        self._url = url
        self._username = username
        self._password = password
        self._token = ""
        self._name = name or username
        self._cache_key = (url, username)

        # Show what we had before the socket is even open, and tell the host
        # about it so it can reply with only what changed.
        held, self._campaign_key = cache.load(url, username)
        if held:
            self.state.replace_all(list(held.values()))

        self._wanted = True
        self._attempt = 0
        self._open()

    def join_as_host(self, url: str, token: str, name: str) -> None:
        """Connect the DM's own app to the server it just started.

        No password: the app already holds the campaign file, so asking for one
        on the same machine would be friction with nothing behind it.
        """
        self.leave()
        self._url = url
        self._token = token
        self._username = ""
        self._password = ""
        self._name = name
        self._role = Role.DM.value
        self._wanted = True
        self._attempt = 0
        self._open()

    def leave(self) -> None:
        self._wanted = False
        self._retry.stop()
        self._connect_timeout.stop()
        self._me = None
        self._members = []
        # The cache is deliberately left in place: it is what makes the next
        # connection cheap, and the host removes anything we may no longer see.
        self._cache_key = None
        self.state.clear()
        if self._socket.state().name != "UnconnectedState":
            self._socket.close()

    def _open(self) -> None:
        log.info("connecting to %s", self._url)
        self._connect_timeout.start(CONNECT_TIMEOUT_MS)
        self._socket.open(QUrl(self._url))

    def _on_connect_timeout(self) -> None:
        url = QUrl(self._url)
        where = url.host() or self._url
        port = url.port(0)
        log.warning("no response from %s within %sms", self._url, CONNECT_TIMEOUT_MS)
        self._socket.abort()
        if self._attempt <= 1:
            self.failed.emit(
                f"No answer from {where}. Check the host is running, and that its "
                f"firewall allows incoming connections on port {port}."
            )

    def _reconnect(self) -> None:
        if self._wanted:
            self._open()

    # ------------------------------------------------------------------- sending

    def send_chat(self, text: str) -> bool:
        return self._send(MessageType.CHAT, text=text)

    def send_roll(self, notation: str) -> bool:
        return self._send(MessageType.ROLL, notation=notation)

    def send_edit(self, entity_id: int, changes: dict) -> bool:
        """Ask the host to change one of your characters.

        No version travels with this. The host knows what it last sent us and
        checks against that, so a client cannot choose which version it claims
        to be editing, nor omit one and be written unconditionally.
        """
        return self._send(MessageType.EDIT, id=entity_id, changes=changes)

    def send_decision(self, proposal_id: int, approve: bool, note: str = "") -> bool:
        """The DM answering a request, with an optional reason for refusing."""
        return self._send(
            MessageType.DECIDE, proposal=proposal_id, approve=approve, note=note
        )

    def _send(self, message_type, **payload) -> bool:
        if not self.is_connected:
            self.failed.emit("Not connected.")
            return False
        self._socket.sendTextMessage(encode(message_type, **payload))
        return True

    # ------------------------------------------------------------------- signals

    def _on_connected(self) -> None:
        self._connect_timeout.stop()
        self._attempt = 0
        known = cache.versions(
            {e["id"]: e for e in self.state.all() if isinstance(e.get("id"), int)}
        )
        if self._token:
            self._socket.sendTextMessage(
                encode(
                    MessageType.HELLO,
                    token=self._token,
                    name=self._name,
                    known=known,
                    campaign=self._campaign_key,
                )
            )
        else:
            self._socket.sendTextMessage(
                encode(
                    MessageType.HELLO,
                    username=self._username,
                    known=known,
                    campaign=self._campaign_key,
                )
            )

    def _on_disconnected(self) -> None:
        self._connect_timeout.stop()
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
            # A campaign snapshot is legitimately large, and this host is one we
            # chose and logged into.
            message = decode(raw, max_bytes=MAX_HOST_FRAME_BYTES)
        except ProtocolError as exc:
            log.warning("unreadable frame from server: %s", exc)
            return

        if message.type == MessageType.CHALLENGE:
            self._answer_challenge(message)

        elif message.type == MessageType.SNAPSHOT:
            entities = message.get("entities")
            entities = entities if isinstance(entities, list) else []
            if message.get("partial"):
                # A delta: keep what we had, apply what changed, drop what the
                # host says we may no longer see.
                for entity in entities:
                    self.state.upsert(entity)
                for entity_id in message.get("gone") or ():
                    if isinstance(entity_id, int):
                        self.state.remove(entity_id)
            else:
                self.state.replace_all(entities)
            self._remember()
            self.state_replaced.emit()

        elif message.type == MessageType.PANEL_NAMES:
            names = message.get("names")
            self.panel_names_received.emit(names if isinstance(names, dict) else {})

        elif message.type == MessageType.HISTORY:
            messages = message.get("messages")
            self.history_received.emit(messages if isinstance(messages, list) else [])

        elif message.type == MessageType.PROPOSALS:
            proposals = message.get("proposals")
            self.proposals_received.emit(
                proposals if isinstance(proposals, list) else []
            )

        elif message.type == MessageType.FACTS:
            facts = message.get("facts")
            self.facts_received.emit(facts if isinstance(facts, list) else [])

        elif message.type == MessageType.ENTITY:
            self.state.upsert(message.get("entity") or {})
            self._remember()

        elif message.type == MessageType.ENTITY_GONE:
            entity_id = message.get("id")
            if isinstance(entity_id, int):
                self.state.remove(entity_id)
                self._remember()

        elif message.type == MessageType.WELCOME:
            key = str(message.get("campaign_key", ""))
            if key and key != self._campaign_key:
                # A different campaign from the one we cached. Ids and versions
                # both restart, so what we are holding is another game's.
                self.state.clear()
                self._campaign_key = key
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
            if message.get("code") in ("bad_login", "refused"):
                # Bad credentials will never come right on their own, so stop
                # rather than hammering the host every second.
                self._wanted = False
                self._retry.stop()
            self.failed.emit(text)

    def _remember(self) -> None:
        """Keep the cache in step with what we hold."""
        if self._cache_key is None:
            return
        url, username = self._cache_key
        cache.save(
            url,
            username,
            {e["id"]: e for e in self.state.all() if isinstance(e.get("id"), int)},
            campaign_key=self._campaign_key,
        )

    def _answer_challenge(self, message) -> None:
        """Prove we know the password without sending it."""
        try:
            salt = bytes.fromhex(str(message.get("salt", "")))
            nonce = bytes.fromhex(str(message.get("nonce", "")))
        except ValueError:
            self.failed.emit("The host sent a login challenge we could not read.")
            return
        if not salt or not nonce:
            return
        verifier = auth.derive_verifier(self._password, salt)
        self._socket.sendTextMessage(
            encode(MessageType.LOGIN, proof=auth.proof(verifier, nonce))
        )
