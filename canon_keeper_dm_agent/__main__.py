"""``canonkeeper-agent`` -- connect to a session and answer when let.

    canonkeeper-agent --url ws://192.168.1.20:8766 --user autopilot

The password is read from ``CANONKEEPER_AGENT_PASSWORD`` or prompted for. It is
never written anywhere: it exists to derive a verifier at login and is then just
a string in memory, the same as in the app.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import sys

from canon_keeper_dm_agent import __version__
from canon_keeper_dm_agent.brain import Brain, BrainUnavailable, is_available, unavailable_hint
from canon_keeper_client import AgentSession, LoginFailed

log = logging.getLogger("canonkeeper.agent")

#: Only answer players. Answering the DM would mean talking over the person who
#: is still in the room, and answering another agent is a loop with a bill.
ANSWERS_TO = ("player",)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canonkeeper-agent",
        description="Run a Canon Keeper table while the DM has autopilot on.",
    )
    parser.add_argument("--url", required=True, help="ws:// or wss:// session address")
    parser.add_argument("--user", required=True, help="the agent login")
    parser.add_argument("--model", default="", help="which model to answer with")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what it would say instead of saying it",
    )
    parser.add_argument("--verbose", action="store_true", help="log every decision")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


async def _run(args) -> int:
    password = os.environ.get("CANONKEEPER_AGENT_PASSWORD") or getpass.getpass(
        f"Password for {args.user}: "
    )
    brain = Brain(model=args.model)

    async def on_said(session: AgentSession, member, text: str) -> None:
        if member.role not in ANSWERS_TO:
            return
        if not session.table.autopilot:
            # The host would refuse it anyway. Not calling the model saves the
            # round trip and the money.
            log.debug("staying quiet: autopilot is off")
            return

        try:
            reply = await asyncio.to_thread(
                brain.answer, session.table, member.label, text
            )
        except BrainUnavailable as exc:
            log.error("cannot answer: %s", exc)
            return
        except Exception:  # noqa: BLE001 - one bad turn must not end the session
            log.exception("the model call failed; staying quiet this turn")
            return

        if not reply:
            return
        if args.dry_run:
            print(f"\n[would say] {reply}\n")
            return
        if not await session.say(reply):
            log.info("not sent -- autopilot went off while we were thinking")

    session = AgentSession(args.url, args.user, password, on_said)
    try:
        await session.run()
    except LoginFailed as exc:
        print(f"Could not log in: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Could not reach {args.url}: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.dry_run and not is_available():
        print(f"No model to answer with: {unavailable_hint()}", file=sys.stderr)
        return 1

    print(
        f"Connecting to {args.url} as {args.user}.\n"
        "It will answer players only while the DM has autopilot switched on.\n"
        "Ctrl-C to stop."
    )
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
