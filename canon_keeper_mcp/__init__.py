"""Talk to your Canon Keeper seat from an MCP client.

Say what you mean -- "I drink the potion and check the door for traps" -- and
have it become real messages at a real table, instead of typing them yourself.

This holds **one login** and has exactly that login's authority. A player's MCP
session can ask; the host decides. There is no privileged path here, which is
why a bug in this package cannot corrupt a campaign.

The honest caveat, which belongs in front of anyone about to use it: whatever
model your MCP client runs will see what this login sees. For a player that is
what their DM shared with them. Point it at a DM login and you have sent your
campaign's secrets to whoever runs that model.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
