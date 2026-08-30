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
from canon_keeper_dm_agent.responder import QUIET_FOR, Responder

log = logging.getLogger("canonkeeper.agent")

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
    parser.add_argument(
        "--pause",
        type=float,
        default=QUIET_FOR,
        metavar="SECONDS",
        help=(
            "how long the table must be quiet before it answers "
            f"(default {QUIET_FOR}). Raise it if it keeps cutting people off."
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="log every decision")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


async def _run(args) -> int:
    password = os.environ.get("CANONKEEPER_AGENT_PASSWORD") or getpass.getpass(
        f"Password for {args.user}: "
    )
    brain = Brain(model=args.model)

    session: AgentSession | None = None

    def answer(table, spoken: list[tuple[str, str]]) -> str:
        """Runs off the event loop; a model call takes seconds."""
        try:
            return brain.answer(table, spoken)
        except BrainUnavailable as exc:
            log.error("cannot answer: %s", exc)
            return ""

    async def say(reply: str) -> None:
        if args.dry_run:
            print(f"\n[would say] {reply}\n")
            return
        if session is not None and not await session.say(reply):
            log.info("not sent -- autopilot went off while we were thinking")

    async def on_busy(on: bool) -> None:
        if session is None:
            return
        await session.set_busy(on)
        if not on:
            # Reported after each turn rather than at the end: a bill you only
            # see when you close the app is not a bill you can act on.
            await session.report_spend(
                tokens_in=brain.total.input_tokens,
                tokens_out=brain.total.output_tokens,
                cached=brain.total.cached_tokens,
                dollars=round(brain.total.dollars, 4),
                turns=brain.turns,
                model=args.model or "",
            )

    responder = Responder(answer, say, quiet_for=args.pause, on_busy=on_busy)

    async def on_said(current: AgentSession, member, text: str) -> None:
        # Returns immediately: the answer happens on its own task, so the
        # socket keeps being read while the model is busy.
        await responder.heard(current, member, text)

    session = AgentSession(args.url, args.user, password, on_said)
    try:
        await session.run()
    except LoginFailed as exc:
        print(f"Could not log in: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Could not reach {args.url}: {exc}", file=sys.stderr)
        return 1
    finally:
        await responder.aclose()
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
