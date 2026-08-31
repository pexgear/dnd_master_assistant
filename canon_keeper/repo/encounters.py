"""Fights: who is in one, in what order, and where they are standing.

Two things a DM tracks in combat and nothing else in this app tracks for them:
an **initiative order** and a **grid**. They are one table each, and one module,
because they are one activity -- the order tells you whose turn it is, the grid
tells you whether they can reach anybody.

Three rules run through this file.

**Off the map is not out of the fight.** ``x``/``y`` of ``None`` means a
combatant is in the order, takes turns, and is simply not standing anywhere --
they fled down the corridor, or they have not burst through the door yet.
Taking them out of the fight is :meth:`EncounterRepo.remove`, a different
operation, because those are two different things a DM means.

**Whose turn it is, is an id.** Not an index into the order: an index shifts the
moment a dead goblin is removed, and the marker jumping a place mid-round is
indistinguishable from a bug.

**The order is a pure function of the rows.** Initiative down, then the
Dexterity tiebreak, then id. No stored ordinal to drift, and a template that
states its initiatives lays out identically every time it is started.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from canon_keeper_protocol import grid

#: A grid smaller than this is not a battlefield, and one larger stops being
#: readable on a laptop before it stops being drawable.
MIN_SIZE = 5
MAX_SIZE = 60

DEFAULT_WIDTH = 20
DEFAULT_HEIGHT = 15


@dataclass(slots=True)
class Combatant:
    id: int | None
    encounter_id: int
    #: The creature. None for a token the DM named and never made an entity of.
    entity_id: int | None = None
    name: str = ""
    #: None until it is rolled, so "not rolled" and "rolled badly" look
    #: different on screen.
    initiative: int | None = None
    tiebreak: int = 0
    x: int | None = None
    y: int | None = None
    added_at: float = 0.0

    @property
    def on_map(self) -> bool:
        return self.x is not None and self.y is not None

    @property
    def sort_key(self) -> tuple:
        """Initiative down, tiebreak down, id up. Total, and stable."""
        return (
            self.initiative is None,
            -(self.initiative if self.initiative is not None else 0),
            -self.tiebreak,
            self.id or 0,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Combatant":
        return cls(
            id=row["id"],
            encounter_id=row["encounter_id"],
            entity_id=row["entity_id"],
            name=row["name"],
            initiative=row["initiative"],
            tiebreak=row["tiebreak"],
            x=row["x"],
            y=row["y"],
            added_at=row["added_at"],
        )


@dataclass(slots=True)
class Encounter:
    id: int | None
    campaign_id: int
    name: str = ""
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    #: 0 until the first turn is taken.
    round: int = 0
    turn_combatant_id: int | None = None
    running: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    version: int = 1

    @property
    def has_begun(self) -> bool:
        return self.round > 0

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        """``(left, top, right, bottom)``, inclusive. 0,0 is the middle."""
        return grid.bounds(self.width, self.height)

    def holds(self, x: int, y: int) -> bool:
        return grid.holds(self.width, self.height, x, y)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Encounter":
        return cls(
            id=row["id"],
            campaign_id=row["campaign_id"],
            name=row["name"],
            width=row["width"],
            height=row["height"],
            round=row["round"],
            turn_combatant_id=row["turn_combatant_id"],
            running=bool(row["running"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )


def order_of(combatants: list[Combatant]) -> list[Combatant]:
    """The initiative order. A pure function, so it can be trusted anywhere."""
    return sorted(combatants, key=lambda c: c.sort_key)


class EncounterRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------- encounters

    def create(
        self,
        campaign_id: int,
        name: str = "",
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        running: bool = True,
    ) -> Encounter:
        """Start a new fight. By default it becomes the one being run."""
        now = time.time()
        width = _clamp(width, MIN_SIZE, MAX_SIZE)
        height = _clamp(height, MIN_SIZE, MAX_SIZE)
        with self._conn:
            if running:
                self._conn.execute(
                    "UPDATE encounter SET running = 0, version = version + 1"
                    " WHERE campaign_id = ? AND running = 1",
                    (campaign_id,),
                )
            cursor = self._conn.execute(
                "INSERT INTO encounter (campaign_id, name, width, height, round,"
                " running, created_at, updated_at, version)"
                " VALUES (?, ?, ?, ?, 0, ?, ?, ?, 1)",
                (campaign_id, name, width, height, int(running), now, now),
            )
        return Encounter(
            id=int(cursor.lastrowid),
            campaign_id=campaign_id,
            name=name,
            width=width,
            height=height,
            running=running,
            created_at=now,
            updated_at=now,
        )

    def get(self, encounter_id: int) -> Encounter | None:
        row = self._conn.execute(
            "SELECT * FROM encounter WHERE id = ?", (encounter_id,)
        ).fetchone()
        return Encounter.from_row(row) if row else None

    def list(self, campaign_id: int) -> list[Encounter]:
        """Newest first, so the fight you just made is the one at the top."""
        rows = self._conn.execute(
            "SELECT * FROM encounter WHERE campaign_id = ?"
            " ORDER BY created_at DESC, id DESC",
            (campaign_id,),
        ).fetchall()
        return [Encounter.from_row(r) for r in rows]

    def current(self, campaign_id: int) -> Encounter | None:
        """The fight being run, or the most recent one, or nothing.

        Falling back to the most recent rather than to nothing means ending a
        fight leaves it on screen to look at, instead of blanking the panel the
        moment the last goblin drops.
        """
        row = self._conn.execute(
            "SELECT * FROM encounter WHERE campaign_id = ?"
            " ORDER BY running DESC, created_at DESC, id DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return Encounter.from_row(row) if row else None

    def running(self, campaign_id: int) -> Encounter | None:
        """The fight actually being run. What players are told about."""
        row = self._conn.execute(
            "SELECT * FROM encounter WHERE campaign_id = ? AND running = 1"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return Encounter.from_row(row) if row else None

    def rename(self, encounter_id: int, name: str) -> None:
        self._write(encounter_id, "name = ?", (name,))

    def resize(self, encounter_id: int, width: int, height: int) -> None:
        """Change the grid. Anyone left outside it is taken off the map.

        Silently keeping a token at (30, 4) on a grid twenty wide would leave
        it invisible and still in the way of every reach and range question the
        DM asks afterwards.
        """
        width = _clamp(width, MIN_SIZE, MAX_SIZE)
        height = _clamp(height, MIN_SIZE, MAX_SIZE)
        left, top, right, bottom = grid.bounds(width, height)
        outside = " AND (x < ? OR x > ? OR y < ? OR y > ?)"
        edges = (left, right, top, bottom)
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET x = NULL, y = NULL"
                " WHERE encounter_id = ?" + outside,
                (encounter_id, *edges),
            )
            # Terrain outside the room simply stops existing. There is nothing
            # to take it off the map *to*, the way there is for a creature.
            self._conn.execute(
                "DELETE FROM obstacle WHERE encounter_id = ?" + outside,
                (encounter_id, *edges),
            )
        self._write(encounter_id, "width = ?, height = ?", (width, height))

    def set_running(self, encounter_id: int, running: bool) -> None:
        """Make this the fight being run, or stop running it."""
        encounter = self.get(encounter_id)
        if encounter is None:
            return
        if running:
            with self._conn:
                self._conn.execute(
                    "UPDATE encounter SET running = 0, version = version + 1"
                    " WHERE campaign_id = ? AND running = 1 AND id != ?",
                    (encounter.campaign_id, encounter_id),
                )
        self._write(encounter_id, "running = ?", (int(bool(running)),))

    def delete(self, encounter_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM combatant WHERE encounter_id = ?", (encounter_id,))
            self._conn.execute("DELETE FROM obstacle WHERE encounter_id = ?", (encounter_id,))
            self._conn.execute("DELETE FROM encounter WHERE id = ?", (encounter_id,))

    # ------------------------------------------------------------- combatants

    def combatants(self, encounter_id: int) -> list[Combatant]:
        """Everyone in the fight, in initiative order."""
        rows = self._conn.execute(
            "SELECT * FROM combatant WHERE encounter_id = ?", (encounter_id,)
        ).fetchall()
        return order_of([Combatant.from_row(r) for r in rows])

    def combatant(self, combatant_id: int) -> Combatant | None:
        row = self._conn.execute(
            "SELECT * FROM combatant WHERE id = ?", (combatant_id,)
        ).fetchone()
        return Combatant.from_row(row) if row else None

    def add(
        self,
        encounter_id: int,
        entity_id: int | None = None,
        name: str = "",
        initiative: int | None = None,
        tiebreak: int = 0,
        x: int | None = None,
        y: int | None = None,
    ) -> Combatant | None:
        """Put a creature in the fight. Returns None if it is already in it."""
        existing = self._by_entity(encounter_id, entity_id)
        if existing is not None:
            return None

        now = time.time()
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO combatant (encounter_id, entity_id, name, initiative,"
                " tiebreak, x, y, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (encounter_id, entity_id, name, initiative, tiebreak, x, y, now),
            )
        self._touch(encounter_id)
        return Combatant(
            id=int(cursor.lastrowid),
            encounter_id=encounter_id,
            entity_id=entity_id,
            name=name,
            initiative=initiative,
            tiebreak=tiebreak,
            x=x,
            y=y,
            added_at=now,
        )

    def remove(self, combatant_id: int) -> None:
        """Out of the fight entirely.

        If it was their turn, the turn moves on first. Otherwise deleting the
        active row leaves the marker on nobody, and the next press of "next
        turn" restarts the order from the top.
        """
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return
        encounter = self.get(combatant.encounter_id)
        if encounter is not None and encounter.turn_combatant_id == combatant_id:
            self.advance(encounter.id, skipping=combatant_id)

        with self._conn:
            self._conn.execute("DELETE FROM combatant WHERE id = ?", (combatant_id,))
        self._touch(combatant.encounter_id)

    def place(self, combatant_id: int, x: int | None, y: int | None) -> bool:
        """Move a token, or take it off the map with ``None, None``.

        Returns False for a square outside the grid or already occupied. The
        occupancy check is here rather than in the panel because the agent will
        eventually send these too, and a rule enforced in one of two callers is
        not a rule.
        """
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return False
        encounter = self.get(combatant.encounter_id)
        if encounter is None:
            return False

        if x is None or y is None:
            x = y = None
        else:
            x, y = int(x), int(y)
            if not encounter.holds(x, y):
                return False
            if self._occupant(encounter.id, x, y, ignoring=combatant_id) is not None:
                return False
            if (x, y) in self.obstacles(encounter.id):
                return False

        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET x = ?, y = ? WHERE id = ?", (x, y, combatant_id)
            )
        self._touch(combatant.encounter_id)
        return True

    def set_initiative(self, combatant_id: int, initiative: int | None) -> None:
        combatant = self.combatant(combatant_id)
        if combatant is None:
            return
        with self._conn:
            self._conn.execute(
                "UPDATE combatant SET initiative = ? WHERE id = ?",
                (initiative, combatant_id),
            )
        self._touch(combatant.encounter_id)

    def at(self, encounter_id: int, x: int, y: int) -> Combatant | None:
        return self._occupant(encounter_id, x, y)

    # ------------------------------------------------------------- what is in the way

    def obstacles(self, encounter_id: int) -> set[tuple[int, int]]:
        """Squares nobody can stand in. A rock, a pillar, an overturned cart."""
        rows = self._conn.execute(
            "SELECT x, y FROM obstacle WHERE encounter_id = ?", (encounter_id,)
        ).fetchall()
        return {(row["x"], row["y"]) for row in rows}

    def toggle_obstacle(self, encounter_id: int, x: int, y: int) -> bool:
        """Put something in the way, or take it out. Returns whether it is there now.

        Refused where somebody is standing: the square would then hold a
        creature and a rock at once, and every rule about the second one would
        have an exception for the first.
        """
        encounter = self.get(encounter_id)
        if encounter is None or not encounter.holds(x, y):
            return False

        if (x, y) in self.obstacles(encounter_id):
            with self._conn:
                self._conn.execute(
                    "DELETE FROM obstacle WHERE encounter_id = ? AND x = ? AND y = ?",
                    (encounter_id, x, y),
                )
            self._touch(encounter_id)
            return False

        if self._occupant(encounter_id, x, y) is not None:
            return False

        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO obstacle (encounter_id, x, y) VALUES (?, ?, ?)",
                (encounter_id, x, y),
            )
        self._touch(encounter_id)
        return True

    # ------------------------------------------------------------------ turns

    def begin(self, encounter_id: int) -> None:
        """Round one, and the highest initiative is up."""
        order = self.combatants(encounter_id)
        if not order:
            return
        self._write(
            encounter_id,
            "round = 1, turn_combatant_id = ?, running = 1",
            (order[0].id,),
        )

    def advance(self, encounter_id: int, skipping: int | None = None) -> None:
        """Next turn, and next round when the order wraps.

        ``skipping`` is the combatant about to be deleted. They cannot be handed
        the turn we are taking away from them -- but the walk still starts from
        *their* place in the order, because the question is who acts after them,
        not who acts first. Closing the gap before looking sends the turn back
        to the top of the round, and somebody acts twice.
        """
        full = self.combatants(encounter_id)
        if not [c for c in full if c.id != skipping]:
            self._write(encounter_id, "turn_combatant_id = NULL")
            return

        encounter = self.get(encounter_id)
        if encounter is None:
            return
        if not encounter.has_begun:
            self.begin(encounter_id)
            return

        ids = [c.id for c in full]
        if encounter.turn_combatant_id not in ids:
            # Whoever was up left the fight without this being told about it.
            # The top of the order is the honest place to resume, and the round
            # does not advance on a correction.
            self._write(
                encounter_id,
                "turn_combatant_id = ?",
                (next(c.id for c in full if c.id != skipping),),
            )
            return

        position = ids.index(encounter.turn_combatant_id) + 1
        wrapped = False
        for _ in range(len(ids) + 1):
            if position >= len(ids):
                position, wrapped = 0, True
            if ids[position] != skipping:
                break
            position += 1

        if wrapped:
            self._write(
                encounter_id,
                "round = round + 1, turn_combatant_id = ?",
                (ids[position],),
            )
        else:
            self._write(encounter_id, "turn_combatant_id = ?", (ids[position],))

    def end(self, encounter_id: int) -> None:
        """The fight is over. Everything stays; only the clock stops."""
        self._write(
            encounter_id, "running = 0, round = 0, turn_combatant_id = NULL", ()
        )

    def clear(self, encounter_id: int) -> None:
        """Empty the fight without deleting it -- the room, kept, and nobody in it.

        The obstacles stay. They are the room rather than the fight, and a DM
        who laid out a cave does not want it back as a flat field because the
        goblins in it changed.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE encounter SET turn_combatant_id = NULL WHERE id = ?",
                (encounter_id,),
            )
            self._conn.execute(
                "DELETE FROM combatant WHERE encounter_id = ?", (encounter_id,)
            )
        self._write(encounter_id, "round = 0", ())

    # ---------------------------------------------------------------- helpers

    def _by_entity(self, encounter_id: int, entity_id: int | None) -> Combatant | None:
        if entity_id is None:
            return None
        row = self._conn.execute(
            "SELECT * FROM combatant WHERE encounter_id = ? AND entity_id = ?",
            (encounter_id, entity_id),
        ).fetchone()
        return Combatant.from_row(row) if row else None

    def _occupant(
        self, encounter_id: int, x: int, y: int, ignoring: int | None = None
    ) -> Combatant | None:
        row = self._conn.execute(
            "SELECT * FROM combatant WHERE encounter_id = ? AND x = ? AND y = ?"
            " AND id != ?",
            (encounter_id, x, y, ignoring or -1),
        ).fetchone()
        return Combatant.from_row(row) if row else None

    def _write(self, encounter_id: int, assignment: str = "", params: tuple = ()) -> None:
        """One write, and every write bumps the version and the clock."""
        clauses = ([assignment] if assignment else []) + [
            "updated_at = ?",
            "version = version + 1",
        ]
        with self._conn:
            self._conn.execute(
                f"UPDATE encounter SET {', '.join(clauses)} WHERE id = ?",
                (*params, time.time(), encounter_id),
            )

    def _touch(self, encounter_id: int) -> None:
        """A combatant changed, so the encounter did. One version for both.

        Clients are sent the whole fight at once -- it is a few hundred bytes --
        so there is nothing to gain from versioning the halves separately, and a
        map whose tokens and order can disagree about how current they are is
        worse than one that cannot.
        """
        self._write(encounter_id)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))
