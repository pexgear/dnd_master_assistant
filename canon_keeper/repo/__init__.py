"""Data access. Panels reach these through ``ctx.repos``."""

from __future__ import annotations

import sqlite3

from canon_keeper.repo.campaigns import CampaignRepo
from canon_keeper.repo.entities import Entity, EntityRepo
from canon_keeper.repo.facts import Fact, FactRepo
from canon_keeper.repo.layouts import LayoutRepo
from canon_keeper.repo.settings import SettingsRepo


class Repos:
    """One container so :class:`~canon_keeper.plugin.AppContext` stays small."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.campaigns = CampaignRepo(conn)
        self.entities = EntityRepo(conn)
        self.facts = FactRepo(conn)
        self.layouts = LayoutRepo(conn)
        self.settings = SettingsRepo(conn)


__all__ = [
    "Repos",
    "Entity",
    "EntityRepo",
    "Fact",
    "FactRepo",
    "CampaignRepo",
    "LayoutRepo",
    "SettingsRepo",
]
