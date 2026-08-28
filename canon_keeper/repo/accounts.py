"""Who is allowed into a campaign, and which character they play."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from canon_keeper.net import auth


@dataclass(slots=True)
class Account:
    id: int
    campaign_id: int
    username: str
    display_name: str
    role: str
    character_entity_id: int | None
    disabled: bool
    created_at: float
    last_seen_at: float | None
    salt: bytes = b""
    verifier: bytes = b""

    @property
    def is_dm(self) -> bool:
        return self.role == "dm"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Account":
        return cls(
            id=row["id"],
            campaign_id=row["campaign_id"],
            username=row["username"],
            display_name=row["display_name"],
            role=row["role"],
            character_entity_id=row["character_entity_id"],
            disabled=bool(row["disabled"]),
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            salt=bytes(row["salt"]),
            verifier=bytes(row["verifier"]),
        )


class AccountRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---------------------------------------------------------------- writes

    def create(
        self,
        campaign_id: int,
        username: str,
        password: str,
        *,
        role: str = "player",
        display_name: str = "",
        character_entity_id: int | None = None,
    ) -> Account:
        username = username.strip()
        if not username:
            raise ValueError("a username is required")
        if self.by_username(campaign_id, username) is not None:
            raise ValueError(f"{username!r} is already taken")

        salt, verifier = auth.make_credentials(password)
        now = time.time()
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO account (campaign_id, username, display_name, role, salt,
                                     verifier, character_entity_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    username,
                    display_name.strip() or username,
                    role if role in ("dm", "player") else "player",
                    salt,
                    verifier,
                    character_entity_id,
                    now,
                ),
            )
        return self.get(int(cur.lastrowid))  # type: ignore[return-value]

    def set_password(self, account_id: int, password: str) -> None:
        salt, verifier = auth.make_credentials(password)
        with self._conn:
            self._conn.execute(
                "UPDATE account SET salt = ?, verifier = ? WHERE id = ?",
                (salt, verifier, account_id),
            )

    def set_character(self, account_id: int, entity_id: int | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE account SET character_entity_id = ? WHERE id = ?",
                (entity_id, account_id),
            )

    def set_disabled(self, account_id: int, disabled: bool) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE account SET disabled = ? WHERE id = ?", (int(disabled), account_id)
            )

    def rename(self, account_id: int, display_name: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE account SET display_name = ? WHERE id = ?",
                (display_name.strip(), account_id),
            )

    def touch(self, account_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE account SET last_seen_at = ? WHERE id = ?", (time.time(), account_id)
            )

    def delete(self, account_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM account WHERE id = ?", (account_id,))

    # ----------------------------------------------------------------- reads

    def get(self, account_id: int) -> Account | None:
        row = self._conn.execute(
            "SELECT * FROM account WHERE id = ?", (account_id,)
        ).fetchone()
        return Account.from_row(row) if row else None

    def by_username(self, campaign_id: int, username: str) -> Account | None:
        row = self._conn.execute(
            "SELECT * FROM account WHERE campaign_id = ?"
            " AND username = ? COLLATE NOCASE",
            (campaign_id, username.strip()),
        ).fetchone()
        return Account.from_row(row) if row else None

    def list(self, campaign_id: int) -> list[Account]:
        rows = self._conn.execute(
            "SELECT * FROM account WHERE campaign_id = ?"
            " ORDER BY role DESC, username COLLATE NOCASE",
            (campaign_id,),
        ).fetchall()
        return [Account.from_row(r) for r in rows]

    def players(self, campaign_id: int) -> list[Account]:
        return [a for a in self.list(campaign_id) if not a.is_dm]

    def authenticate(self, campaign_id: int, username: str, nonce: bytes, offered: str):
        """Return the Account if the proof checks out, else None.

        Callers must not distinguish "no such user" from "wrong password" in
        anything they show, or the login screen becomes a way to enumerate the
        campaign's players.
        """
        account = self.by_username(campaign_id, username)
        if account is None or account.disabled:
            return None
        if not auth.verify(account.verifier, nonce, offered):
            return None
        return account
