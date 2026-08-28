from __future__ import annotations

import logging
import os

# Qt must be told to run headless before any QApplication is constructed, which
# pytest-qt does on import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from canon_keeper.bus import Bus
from canon_keeper.db import connect, migrate
from canon_keeper.plugin import AppContext
from canon_keeper.repo import Repos


@pytest.fixture
def conn(tmp_path):
    """A migrated, empty campaign database in a temporary directory."""
    connection = connect(tmp_path / "campaign.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def repos(conn) -> Repos:
    return Repos(conn)


@pytest.fixture
def ctx(repos) -> AppContext:
    campaign = repos.campaigns.ensure_default("Test Campaign")
    return AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=campaign.id,
    )
