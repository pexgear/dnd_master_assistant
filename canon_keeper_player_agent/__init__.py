"""A machine sitting in one player's chair.

One instance per character, and that is the whole design rather than a
deployment detail. A character handed to autopilot used to be played by the
*DM's* agent, which sees every secret in the campaign -- so a handed-over
character knew where the ambush was and walked around it. That looks like good
play and it is cheating, invisibly.

This connects on a **seat token** instead, which the host mints for one account
and one character. It is admitted as that player and sees exactly what that
player sees: the projection is enforced by the host, not observed here. Two
characters handed over are two processes with two views, so neither knows what
the other was told.

What it may *do* is narrower than what it may see -- move, swing and end the
turn of its one character, on that character's turn -- and that too is the
host's rule, not this package's manners.
"""

#: Its own, deliberately. Importing the app for a version string would make
#: this package depend on 660 MB of Qt and -- far worse -- on something that can
#: open a campaign database. Nothing outside the app imports the app.
__version__ = "0.1.0"

from canon_keeper_player_agent.tactics import Decision, decide

__all__ = ["Decision", "decide", "__version__"]
