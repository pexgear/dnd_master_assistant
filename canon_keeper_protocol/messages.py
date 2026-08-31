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

#: Bumped when the contract grows, not only when it breaks. A build that does
#: not know about encounters would connect happily and show a table no map --
#: half-working, which is the state this number exists to prevent.
PROTOCOL_VERSION = 3

#: No O/0 or I/1 -- these get read aloud across a table.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6

MAX_NAME_LENGTH = 32
MAX_CHAT_LENGTH = 2000
MAX_NOTATION_LENGTH = 64
#: Cap on a frame *we accept from a client*. Clients are untrusted, so this
#: stops one making the host parse megabytes.
MAX_FRAME_BYTES = 16 * 1024

#: Cap on a frame accepted *from the host we logged into*. Much larger, because
#: a snapshot of a whole campaign legitimately runs to hundreds of kilobytes and
#: the client chose this host and authenticated to it. Still bounded, so a
#: compromised or broken host cannot exhaust memory.
MAX_HOST_FRAME_BYTES = 8 * 1024 * 1024


class SystemKind(StrEnum):
    """Why a system line was sent, so a reader can filter the noise.

    The distinction is not decoration. "Marco joined" is chatter nobody needs
    to see twice; "your DM said no, and here is why" is addressed to one person
    and must never be hidden by a filter meant for the former.
    """

    #: Broadcast housekeeping: joins, leaves, autopilot switching.
    CHATTER = "chatter"
    #: Sent to one person, about something they did or must know.
    NOTICE = "notice"


class Role(StrEnum):
    DM = "dm"
    PLAYER = "player"
    #: An autopilot login, standing in for the DM while they let it. Named on
    #: the roster rather than disguised as a DM: a table deserves to know when
    #: it is talking to a machine.
    AGENT = "agent"


class MessageType(StrEnum):
    # client -> server
    HELLO = "hello"        # {username} or {token} for the host's own app
    LOGIN = "login"        # {proof} -- answers a challenge
    CHAT = "chat"
    ROLL = "roll"
    EDIT = "edit"          # {id, changes} -- a player editing their own PC
    DECIDE = "decide"      # {proposal, approve} -- the DM answering one
    BUSY = "busy"          # {on} -- I am composing something
    SPENT = "spent"        # {tokens_in, tokens_out, dollars, turns} -- agent only
    TROUBLE = "trouble"    # {message} -- the agent could not answer. To DMs.
    # Running a fight. The DM's app sends these, and so may an agent while
    # autopilot is on -- one door, one set of checks, rather than a privileged
    # path for the app and a lesser one for everything else.
    MOVE = "move"          # {combatant, x, y} -- x/y null takes it off the map
    TURN = "turn"          # {action} -- begin | next | end
    INITIATIVE = "initiative"  # {combatant, value} -- value null unrolls it
    FIGHT = "fight"        # {name, width, height} -- start a new one
    ENLIST = "enlist"      # {entity, x, y, initiative} -- into the fight
    TERRAIN = "terrain"    # {x, y, on} -- something in the way, or not
    #: A formalised turn, put to the player whose character it is. The agent
    #: writes it, the host checks it, and nothing happens until they say yes.
    PROPOSE = "propose"    # {combatant, move, target, weapon, text}
    ACTED = "acted"        # {id, accept, note} -- the player's answer

    # server -> client
    CHALLENGE = "challenge"  # {salt, nonce}
    WELCOME = "welcome"
    SNAPSHOT = "snapshot"    # everything this account may see
    PANEL_NAMES = "panels"   # what the DM calls each panel
    PROPOSALS = "proposals"  # build changes waiting on the DM
    HISTORY = "history"      # what was said before you arrived
    ENTITY = "entity"        # one entity added or changed
    ENTITY_GONE = "gone"     # {id} -- deleted, or no longer shared with you
    FACTS = "facts"          # the canon log. DM viewers only, never a player.
    ENCOUNTER = "encounter"  # {encounter} -- the fight, or null for none
    ACTION = "action"        # a turn waiting on the player whose turn it is
    ACTION_GONE = "action_gone"  # {id} -- answered, withdrawn, or overtaken
    AUTOPILOT = "autopilot"  # {on, by} -- whether the agent is answering
    BUSY_NOW = "busy_now"    # {member, on} -- who is composing, for everyone
    SPEND = "spend"          # what the agent has cost. DM viewers only.
    REFUSED = "refused"      # {id, reason} -- your request was turned down
    ERROR = "error"
    ROSTER = "roster"
    SAID = "said"
    ROLLED = "rolled"
    SYSTEM = "system"        # {text, kind} -- see SystemKind


@dataclass(slots=True)
class Member:
    id: str
    name: str
    role: str
    #: The character this person is playing. Chat shows this in preference to
    #: the account name -- at the table people are their characters.
    character: str = ""

    @property
    def label(self) -> str:
        return self.character or self.name

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Member":
        return cls(
            id=str(raw.get("id", "")),
            name=clean_name(raw.get("name", "")),
            role=raw.get("role") if raw.get("role") in tuple(Role) else Role.PLAYER.value,
            character=str(raw.get("character", ""))[:MAX_NAME_LENGTH],
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


def decode(raw: str, max_bytes: int = MAX_FRAME_BYTES) -> Message:
    """Parse a frame.

    ``max_bytes`` differs by direction: small for what a host accepts from a
    client, large for what a client accepts from its host. One limit for both
    would either let a client flood the host or stop a legitimate snapshot of a
    large campaign from arriving.
    """
    if len(raw.encode("utf-8", "ignore")) > max_bytes:
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
