"""Wire format.

Newline-free JSON text frames over WebSocket. Deliberately small and versioned:
a mismatched ``v`` is rejected at the door with a readable reason rather than
half-working.

Everything a client sends is treated as hostile input -- lengths are capped and
unknown message types are dropped, because the person on the other end is on
your LAN and may be running a modified build.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = 1

#: No O/0 or I/1 -- these get read aloud across a table.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6

MAX_NAME_LENGTH = 32
MAX_CHAT_LENGTH = 2000
MAX_NOTATION_LENGTH = 64
MAX_FRAME_BYTES = 16 * 1024


class Role(StrEnum):
    DM = "dm"
    PLAYER = "player"


class MessageType(StrEnum):
    # client -> server
    HELLO = "hello"
    CHAT = "chat"
    ROLL = "roll"

    # server -> client
    WELCOME = "welcome"
    ERROR = "error"
    ROSTER = "roster"
    SAID = "said"
    ROLLED = "rolled"
    SYSTEM = "system"


@dataclass(slots=True)
class Member:
    id: str
    name: str
    role: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Member":
        return cls(
            id=str(raw.get("id", "")),
            name=clean_name(raw.get("name", "")),
            role=raw.get("role") if raw.get("role") in tuple(Role) else Role.PLAYER.value,
        )


@dataclass(slots=True)
class Message:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


class ProtocolError(ValueError):
    """The frame could not be understood, and the reason is safe to show."""


def new_join_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def new_member_id() -> str:
    return secrets.token_hex(8)


def clean_name(raw: Any) -> str:
    name = str(raw or "").strip().replace("\n", " ")
    return name[:MAX_NAME_LENGTH] or "Anonymous"


def normalise_code(raw: Any) -> str:
    """Codes are read aloud, so accept lowercase and stray spaces or dashes."""
    return "".join(str(raw or "").upper().split()).replace("-", "")


def encode(message_type: str | MessageType, **payload: Any) -> str:
    return json.dumps(
        {"v": PROTOCOL_VERSION, "t": str(message_type), "ts": time.time(), "d": payload},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def decode(raw: str) -> Message:
    if len(raw.encode("utf-8", "ignore")) > MAX_FRAME_BYTES:
        raise ProtocolError("message too large")
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProtocolError("not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ProtocolError("expected an object")

    version = parsed.get("v")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"protocol version {version}, this build speaks {PROTOCOL_VERSION}"
        )

    message_type = parsed.get("t")
    if not isinstance(message_type, str):
        raise ProtocolError("missing message type")

    payload = parsed.get("d")
    if not isinstance(payload, dict):
        payload = {}

    ts = parsed.get("ts")
    return Message(
        type=message_type,
        payload=payload,
        ts=float(ts) if isinstance(ts, (int, float)) else time.time(),
    )
