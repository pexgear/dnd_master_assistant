"""Data access. Panels reach these through ``ctx.repos``."""

from __future__ import annotations

import sqlite3

from canon_keeper.repo.accounts import Account, AccountRepo
from canon_keeper.repo.campaigns import CampaignRepo
from canon_keeper.repo.entities import Entity, EntityRepo, StaleWrite
from canon_keeper.repo.facts import Fact, FactRepo
from canon_keeper.repo.layouts import LayoutRepo
from canon_keeper.repo.sessions import Session, SessionRepo, Utterance, UtteranceRepo
from canon_keeper.repo.settings import SettingsRepo
from canon_keeper.repo.shares import Share, ShareRepo


class Repos:
    """One container so :class:`~canon_keeper.plugin.AppContext` stays small."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.accounts = AccountRepo(conn)
        self.campaigns = CampaignRepo(conn)
        self.entities = EntityRepo(conn)
        self.facts = FactRepo(conn)
        self.layouts = LayoutRepo(conn)
        self.sessions = SessionRepo(conn)
        self.utterances = UtteranceRepo(conn)
        self.settings = SettingsRepo(conn)
        self.shares = ShareRepo(conn)


__all__ = [
    "Repos",
    "Entity",
    "EntityRepo",
    "StaleWrite",
    "Fact",
    "FactRepo",
    "CampaignRepo",
    "LayoutRepo",
    "SettingsRepo",
    "Session",
    "SessionRepo",
    "Utterance",
    "UtteranceRepo",
    "Account",
    "AccountRepo",
    "Share",
    "ShareRepo",
]
