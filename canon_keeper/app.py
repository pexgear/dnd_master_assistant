"""Application bootstrap: open the database, load the panels, show the window."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from canon_keeper import __version__, config
from canon_keeper.bus import Bus
from canon_keeper.db import connect, migrate
from canon_keeper.plugin import AppContext
from canon_keeper.repo import Repos
from canon_keeper.shell.loader import discover_panels
from canon_keeper.shell.main_window import MainWindow


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="canonkeeper", description="Canon Keeper")
    parser.add_argument("--version", action="version", version=f"canon-keeper {__version__}")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="campaign database to open (default: the per-user data directory)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def build_context(db_path: Path, log: logging.Logger) -> tuple[AppContext, object]:
    """Open the database, migrate it, and assemble the panel context."""
    conn = connect(db_path)
    version = migrate(conn)
    log.info("database %s at schema version %s", db_path, version)

    repos = Repos(conn)
    campaign = repos.campaigns.ensure_default()
    ctx = AppContext(repos=repos, bus=Bus(), log=log, campaign_id=campaign.id)
    return ctx, conn


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = config.setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    log.info("Canon Keeper %s starting", __version__)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Canon Keeper")
    app.setApplicationVersion(__version__)
    app.setOrganizationName(config.APP_AUTHOR)
    # Fusion keeps the three platforms looking like the same application.
    app.setStyle("Fusion")

    db_path = args.db or config.default_db_path()
    try:
        ctx, _conn = build_context(db_path, log)
    except Exception as exc:  # noqa: BLE001 - show the user something actionable
        log.exception("could not open the campaign database")
        QMessageBox.critical(
            None,
            "Canon Keeper",
            f"Could not open the campaign database:\n\n{db_path}\n\n{exc}",
        )
        return 1

    panels, errors = discover_panels(log)
    if not panels:
        log.warning("no panels were discovered; is the package installed?")

    window = MainWindow(ctx, panels, errors, log)
    window.show()
    return app.exec()
