"""An agent that can run a table while the DM lets it.

Canon Keeper is for a human DM. This package is what they hand the table to when
they want a break, a second voice, or a shopkeeper haggled with while they read
ahead -- and it stops the moment they take it back.

It is a **client**, not a component. It holds a socket and a login and knows
only what the host chose to send it. Two things follow from that, and both are
the reason it is built this way:

- It cannot write to the campaign. Like any client it can ask, and the host
  decides. There is no path from here to the database.
- It cannot speak unless autopilot is on. The host refuses its chat otherwise,
  so "off" is enforced at the table rather than observed by the agent.

Run it with ``canonkeeper-agent``; see ``__main__``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
