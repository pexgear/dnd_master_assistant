"""A headless connection to a Canon Keeper session.

The app's own client is built on Qt. This one is not, because an agent has no
window and should not need 660 MB of it to hold a socket. It speaks exactly the
same protocol -- and being a second, independent implementation is the point: an
ambiguity both ends share is invisible until something else has to read it.

What it deliberately does not do: reach into a campaign database. It holds a
socket and a login, and everything it learns arrives over the wire. That is what
keeps "the agent has exactly the authority its login has" a fact about the
system rather than a promise about the code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import websockets

from canon_keeper_protocol import (
    MAX_HOST_FRAME_BYTES,
    Member,
    Message,
    MessageType,
    ProtocolError,
    auth,
    decode,
    encode,
)

log = logging.getLogger("canonkeeper.agent.session")

#: How long to wait for the host to answer the handshake before giving up.
LOGIN_TIMEOUT = 20.0

#: How long to wait for a polite close before dropping the socket. The library
#: default is ten seconds, which is a long time to hold a process open for a
#: goodbye nobody is waiting to hear -- and it is felt every time the agent is
#: stopped or a test tears one down.
CLOSE_TIMEOUT = 2.0


class LoginFailed(RuntimeError):
    """The host refused us, and the reason is safe to print."""


@dataclass
class Table:
    """What the agent knows about the session it is sitting at.

    Kept as plain dicts rather than re-implementing the app's entity classes:
    this is the far end of a wire, and the only consumer is a prompt builder.
    """

    campaign: str = ""
    session: str = ""
    me: Member | None = None
    #: entity id -> the projected entity, as the host chose to send it.
    entities: dict[int, dict] = field(default_factory=dict)
    #: The canon log. Arrives only because this login sees what a DM sees.
    facts: list[dict] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)
    #: Recent chat, oldest first. Bounded -- an agent needs the scene, not the
    #: campaign's whole history, and an unbounded list is a slow leak.
    recent: list[dict] = field(default_factory=list)
    autopilot: bool = False

    RECENT_LIMIT = 40

    def remember(self, speaker: str, role: str, text: str) -> None:
        self.recent.append({"speaker": speaker, "role": role, "text": text})
        if len(self.recent) > self.RECENT_LIMIT:
            del self.recent[: -self.RECENT_LIMIT]


class AgentSession:
    """Connects, logs in, and calls ``on_said`` for everything spoken."""

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        on_said: Callable[["AgentSession", Member, str], Awaitable[None]],
    ) -> None:
        self._url = url
        self._username = username
        self._password = password
        self._on_said = on_said
        self._socket: Any = None
        self.table = Table()

    # ------------------------------------------------------------------ running

    async def run(self) -> None:
        """Connect and pump messages until the connection closes."""
        async with websockets.connect(
            self._url,
            max_size=MAX_HOST_FRAME_BYTES,
            close_timeout=CLOSE_TIMEOUT,
        ) as socket:
            self._socket = socket
            await self._handshake(socket)
            log.info(
                "logged in to %s as %s",
                self.table.campaign or self._url,
                self.table.me.name if self.table.me else self._username,
            )
            async for raw in socket:
                try:
                    message = decode(raw, max_bytes=MAX_HOST_FRAME_BYTES)
                except ProtocolError as exc:
                    log.warning("unreadable frame: %s", exc)
                    continue
                await self._dispatch(message)

    async def say(self, text: str) -> bool:
        """Speak at the table.

        Returns False if the host refuses -- which it does, by design, whenever
        autopilot is off. The refusal is the host's to make; asking first would
        be the agent policing itself, which is not a guarantee.
        """
        if self._socket is None:
            return False
        if not self.table.autopilot:
            # Not a substitute for the host's check, just politeness: no reason
            # to make it refuse something we already know it will.
            log.debug("not speaking: autopilot is off")
            return False
        await self._socket.send(encode(MessageType.CHAT, text=text))
        return True

    async def set_busy(self, on: bool) -> None:
        """Say whether we are composing, so the table is not left guessing."""
        if self._socket is None:
            return
        await self._socket.send(encode(MessageType.BUSY, on=bool(on)))

    async def report_spend(self, **spend) -> None:
        """Tell the host what we have cost. It shows the DM, and nobody else."""
        if self._socket is None:
            return
        await self._socket.send(encode(MessageType.SPENT, **spend))

    # --------------------------------------------------------------- handshake

    async def _handshake(self, socket) -> None:
        await socket.send(encode(MessageType.HELLO, username=self._username))

        async with asyncio.timeout(LOGIN_TIMEOUT):
            challenge = decode(await socket.recv(), max_bytes=MAX_HOST_FRAME_BYTES)
            if challenge.type == MessageType.ERROR:
                raise LoginFailed(str(challenge.get("message", "refused")))
            if challenge.type != MessageType.CHALLENGE:
                raise LoginFailed(f"expected a challenge, got {challenge.type}")

            salt = bytes.fromhex(str(challenge.get("salt", "")))
            nonce = bytes.fromhex(str(challenge.get("nonce", "")))
            if not salt or not nonce:
                raise LoginFailed("the host sent an unusable challenge")

            # The password is turned into a verifier here and never sent. See
            # canon_keeper_protocol.auth for why this is worth the round trip.
            verifier = auth.derive_verifier(self._password, salt)
            await socket.send(
                encode(MessageType.LOGIN, proof=auth.proof(verifier, nonce))
            )

            while True:
                message = decode(await socket.recv(), max_bytes=MAX_HOST_FRAME_BYTES)
                if message.type == MessageType.ERROR:
                    raise LoginFailed(str(message.get("message", "refused")))
                if message.type == MessageType.WELCOME:
                    await self._dispatch(message)
                    return
                await self._dispatch(message)

    # ---------------------------------------------------------------- dispatch

    async def _dispatch(self, message: Message) -> None:
        table = self.table

        if message.type == MessageType.WELCOME:
            table.campaign = str(message.get("campaign", ""))
            table.session = str(message.get("session", ""))
            you = message.get("you")
            if isinstance(you, dict):
                table.me = Member.from_dict(you)

        elif message.type == MessageType.SNAPSHOT:
            if not message.get("partial"):
                table.entities.clear()
            for entity in message.get("entities") or []:
                if isinstance(entity.get("id"), int):
                    table.entities[entity["id"]] = entity
            for gone in message.get("gone") or []:
                table.entities.pop(gone, None)

        elif message.type == MessageType.ENTITY:
            entity = message.get("entity") or {}
            if isinstance(entity.get("id"), int):
                table.entities[entity["id"]] = entity

        elif message.type == MessageType.ENTITY_GONE:
            table.entities.pop(message.get("id"), None)

        elif message.type == MessageType.FACTS:
            facts = message.get("facts")
            table.facts = facts if isinstance(facts, list) else []

        elif message.type == MessageType.AUTOPILOT:
            was = table.autopilot
            table.autopilot = bool(message.get("on"))
            if was != table.autopilot:
                log.info("autopilot %s", "on" if table.autopilot else "off")

        elif message.type == MessageType.ROSTER:
            table.members = [
                Member.from_dict(m)
                for m in (message.get("members") or [])
                if isinstance(m, dict)
            ]

        elif message.type == MessageType.HISTORY:
            for entry in message.get("messages") or []:
                if isinstance(entry, dict) and entry.get("text"):
                    table.remember(
                        str(entry.get("speaker", "")),
                        str(entry.get("role", "")),
                        str(entry["text"]),
                    )

        elif message.type == MessageType.SAID:
            raw_member = message.get("member") or {}
            member = Member.from_dict(raw_member if isinstance(raw_member, dict) else {})
            text = str(message.get("text", ""))
            table.remember(member.label, member.role, text)
            # Our own line coming back. Answering it would be a conversation
            # with ourselves, and a fast one.
            if table.me is not None and member.id == table.me.id:
                return
            await self._on_said(self, member, text)

        elif message.type == MessageType.ROLLED:
            table.remember(
                str((message.get("member") or {}).get("name", "")),
                "",
                str(message.get("text", "") or message.get("result", "")),
            )

        elif message.type == MessageType.ERROR:
            log.warning(
                "host refused: %s (%s)",
                message.get("message"),
                message.get("code"),
            )
