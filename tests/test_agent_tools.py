"""What autopilot can do, as opposed to say.

The agent used to be able to announce that goblins burst from the rubbish and
nothing else -- leaving the DM to build by hand the fight it had just decided
on. These tests are about the other half: that describing a fight and putting it
on everyone's map is one turn, and that every bit of it still goes through the
host rather than round it.

The claim that matters most is the last one in the file: the tools are refused
the instant autopilot goes off, and refused by the *host*, not by the agent
being polite about it.
"""

from __future__ import annotations

import pytest

from canon_keeper_client import Table
from canon_keeper_dm_agent import tools as agent_tools
from canon_keeper_dm_agent.tools import TOOLS, Tools, describe_fight


class _FakeSession:
    """A session that records what was asked of it instead of a socket."""

    def __init__(self, table: Table) -> None:
        self.table = table
        self.asked: list[tuple] = []
        #: What the host says next, in order. Standing in for the ENCOUNTER
        #: frames a real one would send back -- a tool decides whether anything
        #: happened by comparing before with after, so a fake that never changes
        #: is a host that refused everything.
        self.replies: list[dict] = []

    async def start_fight(self, name, width, height) -> bool:
        self.asked.append(("fight", name, width, height))
        return True

    async def enlist(self, entity_id, x=None, y=None, initiative=None) -> bool:
        self.asked.append(("enlist", entity_id, x, y, initiative))
        return True

    async def move(self, combatant_id, x, y) -> bool:
        self.asked.append(("move", combatant_id, x, y))
        return True

    async def set_terrain(self, x, y, on=True) -> bool:
        self.asked.append(("terrain", x, y, on))
        return True

    async def turn(self, action) -> bool:
        self.asked.append(("turn", action))
        return True

    async def propose(self, combatant_id, move=None, target=None, weapon="", text=""):
        self.asked.append(("propose", combatant_id, move, target, weapon, text))
        return True

    async def wait_for_the_fight(self, timeout: float = 2.0) -> dict:
        if self.replies:
            self.table.encounter = self.replies.pop(0)
        return self.table.encounter

    def of_kind(self, kind: str) -> list[tuple]:
        return [call for call in self.asked if call[0] == kind]


@pytest.fixture
def table() -> Table:
    made = Table(campaign="Test")
    made.autopilot = True
    made.entities = {
        1: {"id": 1, "kind": "pc", "name": "Brok Ironfoot"},
        2: {"id": 2, "kind": "pc", "name": "Sable"},
        3: {"id": 3, "kind": "npc", "name": "Goblin with the bow"},
    }
    return made


@pytest.fixture
def session(table) -> _FakeSession:
    return _FakeSession(table)


@pytest.fixture
def tools(session) -> Tools:
    return Tools(session)


def _fight(**overrides) -> dict:
    fight = {
        "id": 1,
        "name": "The cave",
        "width": 10,
        "height": 8,
        "round": 1,
        "turn": 11,
        "obstacles": [[4, 4]],
        "combatants": [
            {"id": 11, "entity": 1, "initiative": 18, "x": 1, "y": 1},
            {"id": 12, "entity": 3, "initiative": 9, "x": 5, "y": 5},
        ],
    }
    fight.update(overrides)
    return fight


# ------------------------------------------------------------------ the schema


def test_every_tool_is_described():
    for tool in TOOLS:
        assert tool["name"]
        assert len(tool["description"]) > 40, f"{tool['name']} says too little"
        assert tool["input_schema"]["type"] == "object"


def test_the_names_are_unique():
    names = [tool["name"] for tool in TOOLS]
    assert len(names) == len(set(names))


def test_every_tool_has_something_that_runs_it(tools):
    """A tool offered to the model and not implemented is a promise it breaks."""
    for tool in TOOLS:
        assert hasattr(tools, f"_{tool['name']}"), f"{tool['name']} has no handler"


# ----------------------------------------------------------------- the refusal


async def test_nothing_happens_while_autopilot_is_off(tools, table, session):
    table.autopilot = False
    answer = await tools.run("start_combat", {"name": "x", "width": 10, "height": 10})
    assert "Autopilot is off" in answer
    assert session.asked == []


async def test_an_unknown_tool_is_a_sentence_not_a_crash(tools):
    assert "no tool called" in await tools.run("summon_dragon", {})


async def test_a_tool_that_throws_does_not_end_the_turn(tools, monkeypatch):
    async def explode(_arguments):
        raise RuntimeError("the socket died")

    monkeypatch.setattr(tools, "_next_turn", explode)
    answer = await tools.run("next_turn", {})
    assert "did not work" in answer
    assert "socket died" in answer


# ------------------------------------------------------------ starting a fight


async def test_starting_a_fight_lays_the_whole_thing_out(tools, session, table):
    table.encounter = _fight()
    await tools.run(
        "start_combat",
        {
            "name": "The cave mouth",
            "width": 16,
            "height": 12,
            "combatants": [
                {"character": "Brok Ironfoot", "x": 3, "y": 2, "initiative": 12},
                {"character": "Goblin with the bow", "x": -6, "y": -4},
            ],
            "obstacles": [[-2, -4], [-2, -3]],
        },
    )

    assert session.of_kind("fight") == [("fight", "The cave mouth", 16, 12)]
    assert session.of_kind("enlist") == [
        ("enlist", 1, 3, 2, 12),
        ("enlist", 3, -6, -4, None),
    ]
    assert session.of_kind("terrain") == [
        ("terrain", -2, -4, True),
        ("terrain", -2, -3, True),
    ]
    assert ("turn", "begin") in session.asked


async def test_a_name_that_is_not_in_the_campaign_is_reported(tools, session, table):
    table.encounter = _fight()
    answer = await tools.run(
        "start_combat",
        {
            "name": "Ambush",
            "width": 10,
            "height": 10,
            "combatants": [
                {"character": "Sable", "x": 1, "y": 1},
                {"character": "A dragon I just made up", "x": 2, "y": 2},
            ],
        },
    )

    assert "A dragon I just made up" in answer
    assert "Only creatures the DM has already made" in answer
    assert len(session.of_kind("enlist")) == 1, "the real one still went in"


async def test_a_first_name_is_enough(tools, session, table):
    """A model told "Brok Ironfoot" will write "Brok"."""
    table.encounter = _fight()
    await tools.run(
        "start_combat",
        {
            "name": "x",
            "width": 10,
            "height": 10,
            "combatants": [{"character": "brok", "x": 0, "y": 0}],
        },
    )
    assert session.of_kind("enlist") == [("enlist", 1, 0, 0, None)]


async def test_a_square_off_the_edge_is_pulled_back_on(tools, session, table):
    """It meant the wall. Losing the placement over it would be worse."""
    table.encounter = _fight()
    await tools.run(
        "start_combat",
        {
            "name": "x",
            "width": 10,
            "height": 8,
            "combatants": [{"character": "Sable", "x": 40, "y": -9}],
        },
    )
    # Ten wide is x -5..4; eight down is y -4..3.
    assert session.of_kind("enlist") == [("enlist", 2, 4, -4, None)]


async def test_a_ridiculous_grid_is_brought_back_to_a_readable_one(tools, session, table):
    table.encounter = _fight()
    await tools.run("start_combat", {"name": "x", "width": 900, "height": 1})
    _kind, _name, width, height = session.of_kind("fight")[0]
    assert width == agent_tools.MAX_GRID
    assert height == 5


# ------------------------------------------------------------------- moving


async def test_moving_someone(tools, session, table):
    table.encounter = _fight()
    session.replies = [
        _fight(
            combatants=[
                {"id": 11, "entity": 1, "x": 3, "y": 3},
                {"id": 12, "entity": 3, "x": 5, "y": 5},
            ]
        )
    ]
    answer = await tools.run("move_on_map", {"character": "Brok", "x": 3, "y": 3})
    assert ("move", 11, 3, 3) in session.asked
    assert "Moved to 3,3" in answer


async def test_a_move_the_host_refused_says_so(tools, session, table):
    """The token stayed where it was, so the answer must not claim otherwise."""
    table.encounter = _fight()
    answer = await tools.run("move_on_map", {"character": "Brok", "x": 5, "y": 5})
    assert "did not move" in answer


async def test_taking_someone_off_the_map(tools, session, table):
    table.encounter = _fight()
    answer = await tools.run(
        "move_on_map", {"character": "Sable", "off_the_map": True}
    )
    # Sable is not in this fight, so there is nothing to take off it.
    assert "not in this fight" in answer

    answer = await tools.run("move_on_map", {"character": "Brok", "off_the_map": True})
    assert ("move", 11, None, None) in session.asked
    assert "still in the order" in answer


async def test_moving_with_no_fight_says_to_start_one(tools):
    assert "no fight running" in await tools.run(
        "move_on_map", {"character": "Brok", "x": 1, "y": 1}
    )


async def test_moving_somebody_who_does_not_exist(tools, table):
    table.encounter = _fight()
    answer = await tools.run("move_on_map", {"character": "Gandalf", "x": 1, "y": 1})
    assert "nobody called" in answer


# ------------------------------------------------------------------ terrain


async def test_putting_something_in_the_way(tools, session, table):
    table.encounter = _fight(obstacles=[[4, 4], [2, 2]])
    answer = await tools.run("set_obstacle", {"x": 2, "y": 2})
    assert ("terrain", 2, 2, True) in session.asked
    assert "in the way at 2,2" in answer


async def test_terrain_the_host_refused_says_so(tools, session, table):
    table.encounter = _fight(obstacles=[])
    answer = await tools.run("set_obstacle", {"x": 1, "y": 1})
    assert "did not change" in answer


# ---------------------------------------------------------- a player's turn
#
# The agent translates; it does not decide. A player character moves because
# its player agreed, and the only thing this tool does is ask them.


async def test_a_players_turn_is_put_to_them(tools, session, table):
    table.encounter = _fight()
    answer = await tools.run(
        "propose_turn",
        {
            "character": "Brok",
            "move": [3, 2],
            "target": "Goblin with the bow",
            "weapon": "battleaxe",
            "text": "Move to 3,2 and attack with a battleaxe.",
        },
    )

    assert session.of_kind("propose") == [
        ("propose", 11, [3, 2], 12, "battleaxe",
         "Move to 3,2 and attack with a battleaxe."),
    ]
    assert "wait for that" in answer, "it must not narrate the outcome"


async def test_a_turn_with_no_move_is_still_a_turn(tools, session, table):
    table.encounter = _fight()
    await tools.run(
        "propose_turn",
        {"character": "Brok", "target": "Goblin with the bow", "text": "Swing."},
    )
    assert session.of_kind("propose")[0][2] is None


async def test_a_square_off_the_grid_is_pulled_back(tools, session, table):
    table.encounter = _fight()
    await tools.run(
        "propose_turn", {"character": "Brok", "move": [99, 99], "text": "Charge."}
    )
    # Ten by eight, so x -5..4 and y -4..3.
    assert session.of_kind("propose")[0][2] == [4, 3]


async def test_proposing_for_somebody_not_in_the_fight(tools, table):
    table.encounter = _fight()
    answer = await tools.run(
        "propose_turn", {"character": "Sable", "text": "Sneak up."}
    )
    assert "not in this fight" in answer


async def test_proposing_at_a_target_that_is_not_there(tools, table):
    table.encounter = _fight()
    answer = await tools.run(
        "propose_turn",
        {"character": "Brok", "target": "Gandalf", "text": "Swing at him."},
    )
    assert "nobody called" in answer


# -------------------------------------------------------------------- turns


async def test_passing_the_turn_reports_the_map(tools, session, table):
    table.encounter = _fight()
    answer = await tools.run("next_turn", {})
    assert ("turn", "next") in session.asked
    assert "round 1" in answer


async def test_ending_the_fight(tools, session, table):
    table.encounter = _fight()
    answer = await tools.run("end_combat", {})
    assert ("turn", "end") in session.asked
    assert "over" in answer


async def test_looking_at_the_map(tools, table):
    table.encounter = _fight()
    answer = await tools.run("look_at_the_map", {})
    assert "Brok Ironfoot at 1,1" in answer
    assert "up now" in answer


# --------------------------------------------------------------- describing


def test_a_fight_reads_as_a_sentence(table):
    said = describe_fight(_fight(), table.entities)
    assert "10 by 8 grid" in said
    assert "round 1" in said
    assert "Goblin with the bow at 5,5" in said
    assert "In the way: 4,4" in said


def test_no_fight_says_so(table):
    assert "no fight running" in describe_fight({}, table.entities)


def test_someone_off_the_map_is_described_as_such(table):
    said = describe_fight(
        _fight(combatants=[{"id": 11, "entity": 1, "x": None, "y": None}]),
        table.entities,
    )
    assert "Brok Ironfoot at off the map" in said


# ------------------------------------------------------- finding a creature


def test_a_name_is_found_the_way_a_person_would(table):
    assert table.entity_named("Sable")["id"] == 2
    assert table.entity_named("sable")["id"] == 2
    assert table.entity_named("Brok")["id"] == 1
    assert table.entity_named("ironfoot")["id"] == 1
    assert table.entity_named("bow")["id"] == 3


def test_a_name_that_is_nobody(table):
    assert table.entity_named("Gandalf") is None
    assert table.entity_named("") is None
