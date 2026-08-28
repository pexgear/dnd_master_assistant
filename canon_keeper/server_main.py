"""Headless session host: ``canonkeeper-server``.

The same :class:`~canon_keeper.net.server.SessionServer` the desktop app runs,
with no GUI around it. Use this when the session should outlive the DM's laptop
-- on a spare machine on the LAN, or on a box somewhere else that everyone can
reach.

Needs no campaign database: chat and dice are the whole protocol today, and none
of it is persisted.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from PySide6.QtCore import QCoreApplication, QTimer

from canon_keeper import __version__, config
from canon_keeper.net.protocol import new_join_code, normalise_code
from canon_keeper.net.server import DEFAULT_PORT, SessionServer


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="canonkeeper-server", description="Host a Canon Keeper session."
    )
    parser.add_argument("--version", action="version", version=f"canon-keeper {__version__}")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("-n", "--name", default="Canon Keeper session")
    parser.add_argument(
        "-c",
        "--code",
        default=None,
        help="fixed join code (default: a new random one each start)",
    )
    parser.add_argument(
        "--no-announce",
        action="store_true",
        help="do not broadcast on the LAN; players must be given the address",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = config.setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    app = QCoreApplication(sys.argv[:1])
    server = SessionServer(args.name, code=normalise_code(args.code) or new_join_code())

    def report_roster(members) -> None:
        log.info("%d connected: %s", len(members), ", ".join(m.name for m in members))

    server.roster_changed.connect(report_roster)
    server.failed.connect(lambda message: log.error("%s", message))

    if not server.start(args.port, announce=not args.no_announce):
        return 1

    print(f"Canon Keeper session {args.name!r}")
    print(f"  port:      {server.port}")
    print(f"  join code: {server.code}")
    print("  Ctrl-C to stop.")

    def shutdown(*_args) -> None:
        log.info("shutting down")
        server.stop()
        app.quit()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)
    # Qt's event loop blocks Python signal delivery unless it wakes periodically.
    heartbeat = QTimer()
    heartbeat.start(400)
    heartbeat.timeout.connect(lambda: None)

    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
