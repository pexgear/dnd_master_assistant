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

from dataclasses import dataclass, field

from canon_keeper.repo.entities import (
    KIND_LOCATION,
    KIND_NPC,
    KIND_PC,
    Entity,
)
from canon_keeper.rules.sheet import BUILD_FIELDS, STATE_FIELDS, is_sheet

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
_OWN_PC_DATA_FIELDS = ("inventory", "player_notes", "sheet")

#: What one player may see of *another* character's sheet. A party knows each
#: other's hit points and what class they are; they do not read each other's
#: notes or inventory.
_SHARED_SHEET_FIELDS = (
    "schema",
    "species",
    "subspecies",
    "class_index",
    "subclass",
    "level",
    "background",
    "abilities",
    "ability_improvements",
    "hp_current",
    "temp_hp",
    "conditions",
    "inspiration",
    "death_saves",
)


def _public_sheet(entity: Entity) -> dict | None:
    """The part of a character sheet other people may see.

    Returned only for player characters. An NPC's sheet is a statblock, and
    sharing that an NPC *exists* must not also reveal how hard it is to kill --
    that is a separate decision the DM has not made.
    """
    if entity.kind != KIND_PC:
        return None
    sheet = entity.data.get("sheet")
    if not is_sheet(sheet):
        return None
    return {key: sheet[key] for key in _SHARED_SHEET_FIELDS if key in sheet}

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
    #: Every character this login owns. A player may have several, and sees the
    #: whole sheet of each.
    owned_entity_ids: set[int] = field(default_factory=set)

    @classmethod
    def dungeon_master(cls) -> "Viewer":
        return cls(account_id=None, is_dm=True)

    def owns(self, entity_id: int | None) -> bool:
        return entity_id is not None and entity_id in self.owned_entity_ids


def project_entity(
    entity: Entity, viewer: Viewer, visible_ids: set[int] | None = None
) -> dict:
    """One entity, reduced to what ``viewer`` may know about it."""
    if viewer.is_dm:
        return _full(entity)

    is_own = viewer.owns(entity.id)
    allowed = _SHARED_DATA_FIELDS.get(entity.kind, _DEFAULT_SHARED_DATA_FIELDS)
    if is_own:
        # Your own character is yours entirely: the whole sheet, no filtering.
        allowed = tuple(allowed) + _OWN_PC_DATA_FIELDS

    data = {key: entity.data[key] for key in allowed if key in entity.data}

    if not is_own:
        public = _public_sheet(entity)
        if public is not None:
            data["sheet"] = public

    projected = {
        "id": entity.id,
        "kind": entity.kind,
        "name": entity.name,
        "summary": entity.summary,
        "data": data,
        "own": is_own,
        "version": entity.version,
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
        "version": entity.version,
        "owner_account_id": entity.owner_account_id,
    }


def visible_entity_ids(repos, campaign_id: int, viewer: Viewer) -> set[int]:
    if viewer.is_dm:
        return {e.id for e in repos.entities.list(campaign_id) if e.id is not None}

    if viewer.account_id is None:
        return set()

    ids = repos.shares.visible_entity_ids(campaign_id, viewer.account_id)
    # Players always see their own characters, shared or not.
    ids |= viewer.owned_entity_ids
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


def snapshot_since(
    repos, campaign_id: int, viewer: Viewer, known: dict[int, int]
) -> tuple[list[dict], list[int]]:
    """What changed since the client last looked.

    Returns ``(entities, gone)``. An entity the client holds but may no longer
    see is listed as gone -- a share taken back must actually leave their
    screen, and silence would leave a stale copy sitting there.
    """
    visible = visible_entity_ids(repos, campaign_id, viewer)
    changed: list[dict] = []

    for entity in repos.entities.list(campaign_id):
        if entity.id not in visible:
            continue
        if known.get(entity.id) == entity.version:
            continue
        changed.append(project_entity(entity, viewer, visible))

    gone = [entity_id for entity_id in known if entity_id not in visible]
    return changed, gone


class EditRefused(PermissionError):
    """A player tried to change something that is not theirs."""


def split_sheet_change(existing: dict, proposed: dict) -> tuple[dict, dict]:
    """Separate what a player may apply from what the DM must confirm.

    Returns ``(state, build)``, each holding only the fields that actually
    differ. Comparing rather than copying matters: a client sends the whole
    sheet back, so without this every save would look like a proposal to change
    everything to what it already is.
    """
    state: dict = {}
    build: dict = {}
    for key, value in proposed.items():
        if existing.get(key) == value:
            continue
        if key in STATE_FIELDS:
            state[key] = value
        elif key in BUILD_FIELDS:
            build[key] = value
    return state, build


def apply_player_edit(
    repos,
    viewer: Viewer,
    entity_id: int,
    changes: dict,
    expected_version: int | None = None,
) -> Entity:
    """Apply a player's edit to one of their own characters, or refuse.

    Silently drops unknown keys rather than failing, so a client sending fields
    we do not recognise cannot use the error to probe what exists.

    ``expected_version`` makes the write conditional: an edit made against a
    sheet that has since moved on is refused rather than quietly overwriting
    whatever happened in between.
    """
    if viewer.is_dm:
        raise EditRefused("this path is for player edits only")
    if not viewer.owns(entity_id):
        raise EditRefused("you can only edit your own character")

    entity = repos.entities.get(entity_id)
    if entity is None:
        raise EditRefused("that character no longer exists")

    incoming_data = changes.get("data") or {}
    if isinstance(incoming_data, dict):
        for key, value in incoming_data.items():
            if key in PLAYER_EDITABLE_DATA_FIELDS:
                entity.data[key] = value

        # A sheet is merged field by field, and only the state half: hit points
        # and conditions apply at once, while level and ability scores are a
        # change to what the character *is* and wait for the DM. Replacing the
        # whole sheet wholesale would let a player smuggle a build change
        # through as state.
        incoming_sheet = incoming_data.get("sheet")
        if isinstance(incoming_sheet, dict):
            existing = entity.data.get("sheet")
            if is_sheet(existing):
                for key, value in incoming_sheet.items():
                    if key in STATE_FIELDS:
                        existing[key] = value
                entity.data["sheet"] = existing
            elif is_sheet(incoming_sheet):
                # No sheet yet: accept the first one, since there is nothing to
                # smuggle a change past.
                entity.data["sheet"] = incoming_sheet

    for column in PLAYER_EDITABLE_COLUMNS:
        if column in changes and isinstance(changes[column], str):
            setattr(entity, column, changes[column].strip())

    # Name, kind, parent and every DM field are untouched by construction: they
    # are simply never read from `changes`.
    return repos.entities.update(entity, expected_version=expected_version)
