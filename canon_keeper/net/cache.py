"""A player's copy of what the host last sent them.

Kept so the app has something to show before it connects, and so reconnecting
transfers what changed rather than everything. A campaign of a hundred entities
is about 96 KB to resend; with versions it is usually a couple of kilobytes, and
reconnecting happens several times an evening.

Stored per session and per login, because two people sharing a machine must not
see each other's view, and one person in two campaigns must not mix them.

What is cached is only ever what the host already sent -- the filtered view. A
revoked share disappears on the next connection, because the host says so. A
player who never reconnects keeps their last copy, which is the accepted cost of
having anything offline at all.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from canon_keeper import config

log = logging.getLogger("canonkeeper.net.cache")

CACHE_DIR = "sessions"
SCHEMA = 1


def _key(url: str, username: str) -> str:
    """A filename that reveals neither the address nor the login."""
    digest = hashlib.blake2b(
        f"{url.strip().lower()}|{username.strip().lower()}".encode("utf-8"),
        digest_size=12,
    ).hexdigest()
    return f"{digest}.json"


def _path(url: str, username: str) -> Path:
    directory = config.data_dir() / CACHE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / _key(url, username)


def load(url: str, username: str) -> dict[int, dict]:
    """What we last held for this session, keyed by entity id."""
    path = _path(url, username)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("could not read the session cache; starting empty")
        return {}

    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return {}

    entities = raw.get("entities")
    if not isinstance(entities, list):
        return {}
    return {
        entity["id"]: entity
        for entity in entities
        if isinstance(entity, dict) and isinstance(entity.get("id"), int)
    }


def save(url: str, username: str, entities: dict[int, dict]) -> None:
    try:
        _path(url, username).write_text(
            json.dumps(
                {"schema": SCHEMA, "entities": list(entities.values())},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
    except OSError:
        log.warning("could not write the session cache")


def forget(url: str, username: str) -> None:
    path = _path(url, username)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            log.warning("could not clear the session cache")


def versions(entities: dict[int, dict]) -> dict[str, int]:
    """``{entity_id: version}`` for what we hold, to send to the host.

    Keys are strings because they cross the wire as JSON, where object keys
    always are -- being explicit here saves a subtle bug at the other end.
    """
    return {
        str(entity_id): int(entity.get("version", 0))
        for entity_id, entity in entities.items()
        if isinstance(entity.get("version"), int)
    }
