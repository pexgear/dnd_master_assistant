"""A fight run with nobody connected is still a fight.

Plenty of evenings are the laptop on the table and four people round it. Taking
a turn used to answer "Go online first -- the dice and the hit points are the
host's", which is true and beside the point: the host is the DM's own app. So
the referee exists whether or not anyone can reach it, and hosting is other
people being able to reach it rather than it being there at all.

What this guards is that the two are the *same* referee. A second, simpler path
for playing alone is how a rule comes to mean one thing at a table and another
thing over the wire.
"""

from __future__ import annotations

import pytest

from canon_keeper.panels.table.widget import TableWidget
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity


@pytest.fixture
def alone(qtbot, ctx):
    """A running fight, a DM's Table panel, and nobody hosting."""
    repos = ctx.repos
    campaign = ctx.campaign_id
    hero = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign,
            kind=KIND_PC,
            name="Brok",
            data={
                "hp": 28,
                "max_hp": 28,
                "sheet": {
                    "schema": 1,
                    "species": "human",
                    "class_index": "fighter",
                    "level": 3,
                    "abilities": {
                        "str": 16, "dex": 14, "con": 14,
                        "int": 10, "wis": 10, "cha": 10,
                    },
                    "equipment": ["battleaxe", "shortbow", "chain-mail"],
                },
            },
        )
    )
    goblin = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign,
            kind=KIND_NPC,
            name="Yeemik",
            data={"hp": 7, "max_hp": 7, "sheet": {"schema": 1, "level": 1}},
        )
    )
    enc = repos.encounters.create(campaign, "The cave", width=12, height=12)
    tokens = {
        "hero": repos.encounters.add(enc.id, hero.id, initiative=20, x=0, y=0),
        # Adjacent, so a melee swing is a swing rather than a reach check.
        "goblin": repos.encounters.add(enc.id, goblin.id, initiative=10, x=1, y=0),
    }
    repos.encounters.begin(enc.id)

    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    assert widget._server is None, "this is the offline case"
    return widget, repos, enc, tokens


def test_the_referee_is_built_on_demand(alone):
    """Not at start-up: opening a campaign to rename it is not an evening."""
    widget, *_ = alone
    assert widget._alone is None

    referee = widget._referee()

    assert referee is not None
    assert widget._alone is referee


def test_the_same_referee_answers_twice(alone):
    widget, *_ = alone
    assert widget._referee() is widget._referee()


def test_it_is_not_listening(alone):
    """Offline means offline. Nothing opened a port to make a fight work."""
    widget, *_ = alone
    assert widget._referee().is_running is False


def test_passing_the_turn_works(alone):
    widget, repos, enc, tokens = alone
    assert repos.encounters.get(enc.id).turn_combatant_id == tokens["hero"].id

    widget._on_turn_requested("next")

    assert repos.encounters.get(enc.id).turn_combatant_id == tokens["goblin"].id


def test_a_round_comes_back_around(alone):
    widget, repos, enc, tokens = alone

    widget._on_turn_requested("next")
    widget._on_turn_requested("next")

    assert repos.encounters.get(enc.id).turn_combatant_id == tokens["hero"].id
    assert repos.encounters.get(enc.id).round == 2


def test_moving_moves(alone):
    widget, repos, enc, tokens = alone

    widget._on_turn_taken({"combatant": tokens["hero"].id, "move": [1, 1]})

    moved = repos.encounters.combatant(tokens["hero"].id)
    assert (moved.x, moved.y) == (1, 1)


def test_a_square_off_the_map_is_refused(alone):
    """The same answer a player gets. One set of rules, not two."""
    widget, repos, enc, tokens = alone
    said = []
    widget._ctx.bus.status_message.connect(said.append)

    widget._on_turn_taken({"combatant": tokens["hero"].id, "move": [99, 99]})

    assert said, "moving off the map passed silently"
    assert "off the map" in said[0]


def test_swinging_costs_the_other_creature_hit_points(alone):
    """Dice, armour class and hit points, with nobody connected to roll for."""
    widget, repos, enc, tokens = alone
    goblin = repos.encounters.combatant(tokens["goblin"].id)
    before = (repos.entities.get(goblin.entity_id).data or {}).get("hp")

    # Enough swings that a run of misses cannot make this flaky, and the fight
    # is what is being tested rather than one roll.
    for _ in range(12):
        widget._on_turn_taken(
            {"combatant": tokens["hero"].id, "target": tokens["goblin"].id}
        )

    after = (repos.entities.get(goblin.entity_id).data or {}).get("hp")
    assert after < before, "twelve swings and the goblin is untouched"


def test_the_map_is_told(alone, qtbot):
    """The DM's own panels read the database, so they have to hear about it."""
    widget, repos, enc, tokens = alone

    with qtbot.waitSignal(widget._ctx.bus.encounter_changed):
        widget._on_turn_taken({"combatant": tokens["hero"].id, "move": [1, 1]})


def test_going_online_retires_the_lone_referee(alone):
    """Two referees is two sets of dice, and one of them is wrong."""
    from canon_keeper.net.server import SessionServer

    widget, repos, enc, tokens = alone
    widget._referee()
    assert widget._alone is not None

    # What _host does once its server is listening.
    widget._server = SessionServer(repos, widget._ctx.campaign_id, parent=widget)
    widget._alone.deleteLater()
    widget._alone = None

    assert widget._referee() is widget._server
