"""A moving creature cannot walk through another one.

``place`` only ever checked the *destination* square -- a walk from one side
of the room to the other went straight through anybody standing between, as
long as wherever it ended up was empty. Obviously wrong: two things cannot
occupy the same square even for a moment, and a creature in the way is a
wall for exactly as long as it is standing there.

Checked the same way everything else about a walk now is: one square of
:func:`grid.steps_between` at a time. The fallen do not block it -- the same
rule that already let a corpse be stepped over rather than closing a corridor
-- and it is not a rule the DM can wave through for an agent, the same tier as
a square already taken or off the edge of the map. Dragging a token to
arrange the board is untouched: that gesture means "put it there", not "walk
there", and the two have never shared a rulebook.
"""

from __future__ import annotations

import pytest

from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity

SHEET = {
    "schema": 1,
    "species": "human",  # speed 30, six squares -- plenty to clear these tests
    "class_index": "fighter",
    "level": 3,
    "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 10},
    "equipment": ["battleaxe", "chain-mail"],
}


@pytest.fixture
def fight(qtbot, repos):
    """A hero at 0,0 and a goblin sitting squarely between it and 4,0."""
    campaign = repos.campaigns.ensure_default("The corridor")
    hero = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok",
               data={"hp": 28, "max_hp": 28, "sheet": SHEET})
    )
    goblin = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Yeemik",
               data={"hp": 7, "max_hp": 7, "sheet": SHEET})
    )
    ally = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Marla",
               data={"hp": 20, "max_hp": 20, "sheet": SHEET})
    )
    encounter = repos.encounters.create(campaign.id, "The corridor", width=20, height=20)
    tokens = {
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=20, x=0, y=0),
        "goblin": repos.encounters.add(encounter.id, goblin.id, initiative=10, x=2, y=0),
        "ally": repos.encounters.add(encounter.id, ally.id, initiative=5, x=0, y=2),
    }
    repos.encounters.begin(encounter.id)
    server = SessionServer(repos, campaign.id, "Corridor session")
    yield server, repos, encounter, tokens, hero, goblin, ally


# -------------------------------------------------------------- the rule


def test_a_creature_squarely_in_the_way_blocks_the_walk(fight):
    server, repos, _encounter, tokens, *_ = fight

    problem = server.take_turn(tokens["hero"].id, move=[4, 0])

    assert "Yeemik" in problem
    assert "way" in problem
    unmoved = repos.encounters.combatant(tokens["hero"].id)
    assert (unmoved.x, unmoved.y) == (0, 0), "the refusal did not stop the move"


def test_an_ally_blocks_just_the_same(fight):
    """No exception for your own side -- two bodies still cannot share a square."""
    server, repos, _encounter, tokens, *_ = fight

    problem = server.take_turn(tokens["hero"].id, move=[0, 4])

    assert "Marla" in problem


def test_stepping_around_it_is_fine(fight):
    """The goblin sits at 2,0. A path that never touches that square is clear."""
    server, repos, _encounter, tokens, *_ = fight

    assert server.take_turn(tokens["hero"].id, move=[2, 3]) == ""

    moved = repos.encounters.combatant(tokens["hero"].id)
    assert (moved.x, moved.y) == (2, 3)


def test_a_clear_path_is_unaffected(fight):
    server, repos, _encounter, tokens, *_ = fight

    assert server.take_turn(tokens["hero"].id, move=[0, -3]) == ""


def test_the_fallen_do_not_block_it(fight):
    """Stepping over a body is already how the game is played."""
    server, repos, _encounter, tokens, hero, goblin, _ally = fight
    entity = repos.entities.get(goblin.id)
    entity.data = {**(entity.data or {}), "hp": 0}
    repos.entities.update(entity)
    repos.encounters.set_down(tokens["goblin"].id, True)

    assert server.take_turn(tokens["hero"].id, move=[4, 0]) == ""


def test_the_destination_itself_is_still_refused_by_place(fight):
    """This rule is about the squares in between -- the last one is somebody else's."""
    server, repos, _encounter, tokens, *_ = fight

    problem = server.take_turn(tokens["hero"].id, move=[2, 0])

    assert problem  # exactly which message it is, is place()'s to decide
    unmoved = repos.encounters.combatant(tokens["hero"].id)
    assert (unmoved.x, unmoved.y) == (0, 0)


def test_arriving_from_off_the_map_is_not_checked(fight):
    """There is nowhere to measure a walk from if you were never on the board."""
    server, repos, encounter, tokens, hero, _goblin, _ally = fight
    repos.encounters.place(tokens["hero"].id, None, None)
    combatant = repos.encounters.combatant(tokens["hero"].id)
    entity = repos.entities.get(hero.id)

    assert server._blocked_path(combatant, entity, 4, 0) == ""


def test_not_moving_at_all_blocks_nothing(fight):
    server, repos, _encounter, tokens, hero, _goblin, _ally = fight
    combatant = repos.encounters.combatant(tokens["hero"].id)
    entity = repos.entities.get(hero.id)

    assert server._blocked_path(combatant, entity, 0, 0) == ""


# -------------------------------------------------------- arranging the board


def test_dragging_straight_through_somebody_is_still_free(fight):
    """A drag means "put it there", not "walk there" -- this rule is about walks."""
    server, repos, _encounter, tokens, *_ = fight

    assert repos.encounters.place(tokens["hero"].id, 4, 0) is True

    moved = repos.encounters.combatant(tokens["hero"].id)
    assert (moved.x, moved.y) == (4, 0)


# --------------------------------------------------------------- not bendable


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def test_an_agents_blocked_move_is_refused_outright(qtbot, fight):
    """Not a rule to put to the DM -- the same tier as a square already taken.

    A speed violation goes to the DM as something that might be waived
    (``bend_requested``); a body in the way does not, because no amount of
    permission makes two creatures share a square.
    """
    server, repos, _encounter, tokens, *_ = fight
    repos.accounts.create(
        server.campaign_id, "autopilot", "let-me-run-it",
        role="agent", display_name="Autopilot",
    )
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    try:
        server.set_autopilot(True, by="the DM")
        agent = _join(qtbot, server, "autopilot", "let-me-run-it")
        bends: list[dict] = []
        agent.bend_requested.connect(bends.append)
        try:
            with qtbot.waitSignal(agent.failed, timeout=5000):
                agent.send_move(tokens["hero"].id, 4, 0)
            assert bends == [], "a body in the way was offered to the DM as a rule"
            assert repos.encounters.combatant(tokens["hero"].id).x == 0
        finally:
            agent.leave()
    finally:
        server.stop()
