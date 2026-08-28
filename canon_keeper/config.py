"""Per-OS paths and persisted settings.

Every write goes through :func:`data_dir` so the app behaves itself on all three
platforms: ``%APPDATA%`` on Windows, ``~/Library/Application Support`` on macOS,
``~/.local/share`` on Linux. Nothing is ever written next to the source tree.

Set ``CANONKEEPER_DATA_DIR`` to override, which is how the tests stay hermetic.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "CanonKeeper"
APP_AUTHOR = "CanonKeeper"

_dirs = PlatformDirs(APP_NAME, APP_AUTHOR, roaming=True)


def _override() -> Path | None:
    raw = os.environ.get("CANONKEEPER_DATA_DIR")
    return Path(raw) if raw else None


def data_dir() -> Path:
    """Directory holding campaign databases and recorded audio."""
    path = _override() or Path(_dirs.user_data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    base = _override()
    path = (base / "logs") if base else Path(_dirs.user_log_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def campaigns_dir() -> Path:
    path = data_dir() / "campaigns"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_db_path() -> Path:
    """The database opened when no other campaign has been chosen."""
    return campaigns_dir() / "default.sqlite3"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Log to stderr and to a rotating-ish file in the per-OS log directory."""
    logger = logging.getLogger("canonkeeper")
    if logger.handlers:  # idempotent: tests and reloads call this repeatedly
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    # Audio device names routinely contain characters the Windows console
    # codepage cannot encode -- a Logitech webcam reports itself in Chinese, for
    # one -- and an unencodable log line would otherwise raise from inside the
    # logging call. Degrade the character, never the log.
    stream = logging.StreamHandler()
    if hasattr(stream.stream, "reconfigure"):
        try:
            stream.stream.reconfigure(errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    try:
        file_handler = logging.FileHandler(log_dir() / "canonkeeper.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        # A read-only or missing log directory must never stop the app booting.
        logger.warning("could not open log file; continuing with console logging only")

    return logger
