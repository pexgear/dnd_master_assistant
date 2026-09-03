"""``canonkeeper-player`` -- one character, played by this process.

Started with a seat token rather than a login, because there is no login to
give: after invitations nobody can make one and the DM does not know their
player's password. The token comes from the host when a character is handed
over, and dies when it is handed back -- so this process is not something that
lingers with a credential, it is something that exists for as long as the
arrangement does.

    canonkeeper-player --url ws://192.168.1.10:8765 --seat SEAT-TOKEN

One instance per character. Two characters handed over are two of these, each
seeing only what its own player sees.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from canon_keeper_player_agent import __version__
from canon_keeper_client.session import AgentSession, LoginFailed
from canon_keeper_player_agent.play import PAUSE, Stand_In

log = logging.getLogger("canonkeeper.player_agent")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="canonkeeper-player",
        description="Play one character while their player is away.",
    )
    parser.add_argument("--url", required=True, help="ws:// or wss:// session address")
    parser.add_argument(
        "--seat",
        default="",
        help=(
            "the seat token the host minted. Prefer CANONKEEPER_SEAT: a token "
            "on the command line is visible to anyone who can list processes"
        ),
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=PAUSE,
        help="seconds to wait before acting, so a DM can change their mind",
    )
    parser.add_argument("--verbose", action="store_true", help="log every decision")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def seat_token(args) -> str:
    """The token, from the environment first.

    The app passes it that way on purpose, the same way it passes the DM
    agent's password: a command line is readable by anyone who can list
    processes, and this buys somebody's character.
    """
    return os.environ.get("CANONKEEPER_SEAT", "") or args.seat


async def run(args) -> int:
    async def said(_session, _member, _text) -> None:
        # A stand-in does not answer the room. It plays its character's turns,
        # and the table is not waiting on it for anything else.
        return None

    stand_in: Stand_In | None = None

    async def encounter(session) -> None:
        if stand_in is not None:
            await stand_in.on_encounter(session)

    async def action(session, proposed) -> None:
        if stand_in is not None:
            await stand_in.on_action(session, proposed)

    session = AgentSession(
        args.url,
        username="",
        password="",
        on_said=said,
        on_encounter=encounter,
        seat=seat_token(args),
        on_action=action,
    )
    stand_in = Stand_In(session, pause=args.pause)

    try:
        await session.run()
    except LoginFailed as exc:
        # The usual reason is the ordinary one: the character was handed back,
        # so the seat no longer exists. That is not an error to shout about.
        log.info("the seat is not open: %s", exc)
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not seat_token(args):
        print(
            "No seat token. Set CANONKEEPER_SEAT, or pass --seat for a "
            "one-off run.",
            file=sys.stderr,
        )
        return 2
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
