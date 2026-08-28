"""Turning canon into what one particular person is allowed to see.

Everything here runs on the host. A player's app is never sent a secret and told
to hide it -- the bytes simply do not contain it. That is the difference between
a filter and a blindfold, and it is why this module exists rather than a
`visible` flag on the widgets.

Two rules hold the whole design up:

* **Allowlists, never denylists.** A field is invisible to players unless it
  appears below. Add ``blackmail_material`` to a character next year and it is
  private by default; with a denylist it would have leaked on the next release.
* **Existence is itself a secret.** An entity that has not been shared is not
  returned at all, so "who is in this city" cannot be used to discover NPCs the
  party has never met.
"""

from __future__ import annotations

from dataclasses import dataclass

from canon_keeper.repo.entities import (
    KIND_LOCATION,
    KIND_NPC,
    KIND_PC,
    Entity,
)

#: Keys inside ``entity.data`` a player may see on a shared entity.
_SHARED_DATA_FIELDS: dict[str, tuple[str, ...]] = {
    KIND_NPC: ("status", "party_knows", "shared_notes"),
    KIND_PC: ("status", "party_knows", "shared_notes", "hp", "max_hp", "conditions"),
    KIND_LOCATION: ("place_type", "shared_notes"),
}
_DEFAULT_SHARED_DATA_FIELDS = ("shared_notes",)

#: Extra keys the owner of a PC sees on their own sheet. DM-authored fields --
#: motive, secrets, voice -- are deliberately absent even here: the DM's notes
#: about a character are the DM's, whoever plays it.
_OWN_PC_DATA_FIELDS = ("inventory", "player_notes")

#: What a player may change on their own PC, and nothing else. Anything not
#: listed is ignored rather than rejected, so an older or modified client cannot
#: widen its own permissions by sending extra keys.
PLAYER_EDITABLE_DATA_FIELDS = frozenset(
    {"hp", "max_hp", "conditions", "inventory", "player_notes", "status"}
)
PLAYER_EDITABLE_COLUMNS = frozenset({"summary"})


@dataclass(slots=True)
class Viewer:
    """Who is asking. The DM sees everything; everyone else sees their shares."""

    account_id: int | None
    is_dm: bool
    own_entity_id: int | None = None

    @classmethod
    def dungeon_master(cls) -> "Viewer":
        return cls(account_id=None, is_dm=True)


def project_entity(
    entity: Entity, viewer: Viewer, visible_ids: set[int] | None = None
) -> dict:
    """One entity, reduced to what ``viewer`` may know about it."""
    if viewer.is_dm:
        return _full(entity)

    is_own = viewer.own_entity_id is not None and entity.id == viewer.own_entity_id
    allowed = _SHARED_DATA_FIELDS.get(entity.kind, _DEFAULT_SHARED_DATA_FIELDS)
    if is_own:
        allowed = tuple(allowed) + _OWN_PC_DATA_FIELDS

    data = {key: entity.data[key] for key in allowed if key in entity.data}

    projected = {
        "id": entity.id,
        "kind": entity.kind,
        "name": entity.name,
        "summary": entity.summary,
        "data": data,
        "own": is_own,
    }

    # A parent is only mentioned if the player can see it too. Otherwise
    # "somewhere inside a place you have never heard of" leaks the place.
    if entity.parent_id is not None and (
        visible_ids is None or entity.parent_id in visible_ids
    ):
        projected["parent_id"] = entity.parent_id
    else:
        projected["parent_id"] = None

    return projected


def _full(entity: Entity) -> dict:
    return {
        "id": entity.id,
        "kind": entity.kind,
        "name": entity.name,
        "summary": entity.summary,
        "data": dict(entity.data),
        "parent_id": entity.parent_id,
        "aliases": list(entity.aliases),
        "own": False,
    }


def visible_entity_ids(repos, campaign_id: int, viewer: Viewer) -> set[int]:
    if viewer.is_dm:
        return {e.id for e in repos.entities.list(campaign_id) if e.id is not None}

    if viewer.account_id is None:
        return set()

    ids = repos.shares.visible_entity_ids(campaign_id, viewer.account_id)
    # Players always see their own character, whether or not anyone shared it.
    if viewer.own_entity_id is not None:
        ids.add(viewer.own_entity_id)
    return ids


def snapshot(repos, campaign_id: int, viewer: Viewer) -> list[dict]:
    """Everything ``viewer`` may see, ready to send."""
    visible = visible_entity_ids(repos, campaign_id, viewer)
    entities = repos.entities.list(campaign_id)
    return [
        project_entity(entity, viewer, visible)
        for entity in entities
        if entity.id in visible
    ]


class EditRefused(PermissionError):
    """A player tried to change something that is not theirs."""


def apply_player_edit(repos, viewer: Viewer, entity_id: int, changes: dict) -> Entity:
    """Apply a player's edit to their own character, or refuse.

    Silently drops unknown keys rather than failing, so a client sending fields
    we do not recognise cannot use the error to probe what exists.
    """
    if viewer.is_dm:
        raise EditRefused("this path is for player edits only")
    if viewer.own_entity_id is None or entity_id != viewer.own_entity_id:
        raise EditRefused("you can only edit your own character")

    entity = repos.entities.get(entity_id)
    if entity is None:
        raise EditRefused("that character no longer exists")

    incoming_data = changes.get("data") or {}
    if isinstance(incoming_data, dict):
        for key, value in incoming_data.items():
            if key in PLAYER_EDITABLE_DATA_FIELDS:
                entity.data[key] = value

    for column in PLAYER_EDITABLE_COLUMNS:
        if column in changes and isinstance(changes[column], str):
            setattr(entity, column, changes[column].strip())

    # Name, kind, parent and every DM field are untouched by construction: they
    # are simply never read from `changes`.
    return repos.entities.update(entity)
