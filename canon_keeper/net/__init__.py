"""Session networking: one host, many clients, over WebSockets.

The host may be the DM's own copy of the app or a headless ``canonkeeper-server``
somewhere else. Both run the same :class:`~canon_keeper.net.server.SessionServer`,
so which one you use is a deployment choice rather than a different codebase.

The DM's app connects to its own server over loopback rather than short-circuiting
to it in-process. That keeps exactly one path through the code: whatever the DM
sees, a player saw arrive the same way.
"""

from canon_keeper_protocol.messages import (
    PROTOCOL_VERSION,
    Member,
    Message,
    MessageType,
    Role,
    decode,
    encode,
    new_join_code,
)

__all__ = [
    "PROTOCOL_VERSION",
    "Member",
    "Message",
    "MessageType",
    "Role",
    "decode",
    "encode",
    "new_join_code",
]
