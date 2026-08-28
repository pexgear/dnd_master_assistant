"""Application bootstrap: choose a campaign, then open the workspace.

The campaign comes first. Everything else -- panels, sessions, sharing -- is
about one campaign, so no window is built until the app knows which one.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from canon_keeper import __version__, campaigns, config
from canon_keeper.bus import Bus
from canon_keeper.db import connect, migrate
from canon_keeper.net.state import SharedState
from canon_keeper.plugin import AppContext
from canon_keeper.repo import Repos
from canon_keeper.shell.loader import discover_panels
from canon_keeper.shell.main_window import MainWindow
from canon_keeper.shell.startup import Launch, choose_campaign
from canon_keeper.shell.theme import ThemeController


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="canonkeeper", description="Canon Keeper")
    parser.add_argument("--version", action="version", version=f"canon-keeper {__version__}")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="open this campaign file directly, skipping the chooser",
    )
    parser.add_argument(
        "--player", action="store_true", help="open the chooser on the join tab"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


def build_context(
    db_path: Path,
    log: logging.Logger,
    role: str = "dm",
    shared: SharedState | None = None,
) -> tuple[AppContext, object]:
    """Open the database, migrate it, and assemble the panel context."""
    conn = connect(db_path)
    version = migrate(conn)
    log.info("database %s at schema version %s", db_path, version)

    repos = Repos(conn)
    campaign = repos.campaigns.ensure_default()
    ctx = AppContext(
        repos=repos,
        bus=Bus(),
        log=log,
        campaign_id=campaign.id,
        role=role,
        shared=shared if shared is not None else SharedState(),
    )
    return ctx, conn


def _resolve_launch(args) -> Launch | None:
    """Decide which campaign to open, asking unless we were told."""
    if args.db is not None:
        return Launch(
            kind="local", path=args.db, name=campaigns.campaign_name_of(args.db)
        )
    return choose_campaign(start_online=args.player)


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

    # The chooser appears before any campaign is open, so its appearance comes
    # from the profile -- otherwise a dark-mode user gets a flash of white.
    profile_ctx, _profile_conn = build_context(config.profile_db_path(), log)
    ThemeController(app, profile_ctx.repos.settings).apply()

    launch = _resolve_launch(args)
    if launch is None:
        log.info("no campaign chosen; exiting")
        return 0

    # A player's settings and dock layout live in their own profile: the
    # campaign they joined is not theirs to write to, and may not be a local
    # file at all.
    db_path = launch.path if launch.kind == "local" else config.profile_db_path()

    try:
        ctx, _conn = build_context(db_path, log, role=launch.role)
    except Exception as exc:  # noqa: BLE001 - show the user something actionable
        log.exception("could not open the campaign")
        QMessageBox.critical(
            None, "Canon Keeper", f"Could not open:\n\n{db_path}\n\n{exc}"
        )
        return 1

    if launch.is_remote:
        # Picked up by the Table panel, which connects as soon as it is built.
        ctx.pending_join = (launch.url, launch.username, launch.password)
        campaigns.remember_remote(launch.url, launch.name, launch.username)

    theme = ThemeController(app, ctx.repos.settings)
    theme.changed.connect(ctx.bus.theme_changed)
    theme.apply()

    panels, errors = discover_panels(log, role=launch.role)
    if not panels:
        log.warning("no panels were discovered; is the package installed?")

    window = MainWindow(ctx, panels, errors, log, theme)
    if launch.is_remote:
        window.setWindowTitle(f"Canon Keeper - {launch.name or launch.url} (player)")
    window.show()
    return app.exec()
