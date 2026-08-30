"""A headless connection to a Canon Keeper session.

The app's own client is built on Qt. This one is not, because nothing that
merely holds a socket should need 660 MB of Qt to do it. It is shared by
everything that talks to a session from outside: the autopilot agent, the MCP
server, and whatever comes next.

Two properties come from where this sits rather than from how it behaves:

- **It cannot reach a campaign.** There is no database here. Everything it
  knows arrived over the wire, filtered by the host for the login it used.
- **It has exactly the authority of that login.** A player login can ask; the
  host decides. An agent login can speak only while autopilot is on.

Neither is a rule this code follows. Both are things it cannot do.
"""

from __future__ import annotations

from canon_keeper_client.session import (
    CLOSE_TIMEOUT,
    LOGIN_TIMEOUT,
    AgentSession,
    LoginFailed,
    Table,
)

__all__ = [
    "CLOSE_TIMEOUT",
    "LOGIN_TIMEOUT",
    "AgentSession",
    "LoginFailed",
    "Table",
]
