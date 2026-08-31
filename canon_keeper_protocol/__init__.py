"""What a Canon Keeper session speaks.

This package is the contract between a host and anything that connects to it:
the frame format, the login exchange, and the dice. Nothing else. It is kept
separate from the app for one reason -- **it depends on nothing but the standard
library**, so a headless client (an agent, a bot, an MCP server) can speak to a
session without installing 660 MB of Qt to do it.

That constraint is enforced by a test, not by good intentions. See
``tests/test_protocol_package.py``.

The other consequence of living here is that the protocol is now an API. Both
ends used to change in one commit; once something outside this repository speaks
it, :data:`PROTOCOL_VERSION` has to mean what it says.
"""

from __future__ import annotations

from canon_keeper_protocol.auth import (
    AuthError,
    MIN_PASSWORD_LENGTH,
    derive_verifier,
    explain,
    proof,
    verify,
)
from canon_keeper_protocol.dice import DiceError, Roll, roll
from canon_keeper_protocol.messages import (
    CODE_LENGTH,
    MAX_CHAT_LENGTH,
    MAX_FRAME_BYTES,
    MAX_HOST_FRAME_BYTES,
    MAX_NAME_LENGTH,
    MAX_NOTATION_LENGTH,
    PROTOCOL_VERSION,
    Member,
    Message,
    MessageType,
    ProtocolError,
    Role,
    SystemKind,
    clean_name,
    decode,
    encode,
    new_join_code,
    new_member_id,
    normalise_code,
)

__all__ = [
    "AuthError",
    "CODE_LENGTH",
    "DiceError",
    "MAX_CHAT_LENGTH",
    "MAX_FRAME_BYTES",
    "MAX_HOST_FRAME_BYTES",
    "MAX_NAME_LENGTH",
    "MAX_NOTATION_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "PROTOCOL_VERSION",
    "Member",
    "Message",
    "MessageType",
    "ProtocolError",
    "Role",
    "Roll",
    "SystemKind",
    "clean_name",
    "decode",
    "derive_verifier",
    "encode",
    "explain",
    "new_join_code",
    "new_member_id",
    "normalise_code",
    "proof",
    "roll",
    "verify",
]
