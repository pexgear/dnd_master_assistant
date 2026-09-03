"""Walking out of somebody's reach, and what it costs.

Without this rule a grid is a diagram. You stroll past the ogre to reach the
wizard behind it, and the ogre may only watch -- so standing next to something
means nothing, and neither does choosing where to stand. This is the rule that
makes the map a map.

Every square of the walk is checked, not only where it starts and ends --
found with the same step-by-step line the move is animated along
(`grid.steps_between`). Checking only the ends missed a creature who cut
straight through an ogre's reach on the way to somewhere else, never adjacent
at either end of the move. Stepping around an enemy and back into its reach,
by contrast, has not really left it, and a rule that fired on every wobble
would punish moving at all -- "did you leave, at any point during the walk" is
the version people actually play, and that is the one checked here.
"""

from __future__ import annotations

import pytest

import canon_keeper.net.server as server_module
from canon_keeper.content import Content
from canon_keeper.net.server import SessionServer
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper.rules import attack


def _sheet(equipment, **overrides) -> dict:
    sheet = {
        "schema": 1,
        "species": "human",
        "class_index": "fighter",
        "level": 3,
        "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 10},
        "equipment": equipment,
        "hp_current": 30,
    }
    sheet.update(overrides)
    return sheet


class _Rolls:
    def __init__(self, *totals: int) -> None:
        self._totals = list(totals)

    def __call__(self, notation: str):
        value = self._totals.pop(0) if self._totals else 18

        class _Result:
            total = value
            rolls = [value]

        return _Result()


@pytest.fixture
def content(repos) -> Content:
    return Content(repos.settings)


@pytest.fixture
def fight(qtbot, repos):
    """A hero standing next to a goblin, with room to walk away."""
    campaign = repos.campaigns.ensure_default("Reach")
    hero = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Brok",
            data={
                "hp": 30,
                "max_hp": 30,
                "sheet": _sheet(["battleaxe", "chain-mail"]),
            },
        )
    )
    goblin = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Yeemik",
            data={
                "hp": 12,
                "max_hp": 12,
                "sheet": _sheet(["scimitar"], overrides={"ac": 10, "hp_max": 12}),
            },
        )
    )
    archer = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Droop",
            data={
                "hp": 7,
                "max_hp": 7,
                "sheet": _sheet(["shortbow"], overrides={"ac": 10, "hp_max": 7}),
            },
        )
    )

    encounter = repos.encounters.create(campaign.id, "The cave", width=16, height=16)
    tokens = {
        # Standing on adjacent squares, so a step away leaves reach.
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=20, x=0, y=0),
        "goblin": repos.encounters.add(encounter.id, goblin.id, initiative=10, x=1, y=0),
        "archer": repos.encounters.add(encounter.id, archer.id, initiative=5, x=-1, y=0),
    }
    repos.encounters.begin(encounter.id)

    server = SessionServer(repos, campaign.id, "Reach session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, repos, encounter, tokens, hero, goblin, archer
    server.stop()


def _hp(repos, entity) -> int:
    return repos.entities.get(entity.id).data["hp"]


# ------------------------------------------------------------------- the rule


def test_walking_away_is_swung_at(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert _hp(repos, hero) < before, "the goblin watched them walk away"


def test_staying_in_reach_is_not(fight, monkeypatch):
    """Shuffling round somebody is not escaping them."""
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    before = _hp(repos, hero)

    # 0,0 -> 1,1 is still one square from the goblin at 1,0.
    server._do_move(tokens["hero"].id, 1, 1, spending=True)

    assert _hp(repos, hero) == before


def test_an_archer_cannot_stop_you_leaving(fight, monkeypatch):
    """A bow does not hold ground, which is why archers want a friend in front."""
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, goblin, _archer = fight
    # Take the goblin out of it so only the archer is adjacent.
    repos.encounters.place(tokens["goblin"].id, 6, 6)
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert _hp(repos, hero) == before


def test_a_dm_arranging_the_board_provokes_nothing(fight, monkeypatch):
    """Dragging tokens about before anybody rolls is not somebody walking."""
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, 0, 4, spending=False)

    assert _hp(repos, hero) == before


def test_cutting_through_reach_without_stopping_still_provokes(fight, monkeypatch):
    """Never adjacent at the start, never adjacent at the end -- and still hit.

    Start-and-end-only missed exactly this: a walk that enters an enemy's
    reach and leaves it again inside one move, with nothing at either end to
    show for it.
    """
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    # The goblin stays at 1,0. A straight walk up column 0 passes within one
    # square of it at y=-1,0,1 and is nowhere near it at either end.
    repos.encounters.place(tokens["hero"].id, 0, -3)
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, 0, 3, spending=True)

    assert _hp(repos, hero) < before, "walking straight through reach was free"


def test_never_getting_close_enough_provokes_nothing(fight, monkeypatch):
    """The other half of the same check: a walk that never enters is not a leave."""
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    # Column -3 stays four squares from the goblin at 1,0 the whole way.
    repos.encounters.place(tokens["hero"].id, -3, -3)
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, -3, 3, spending=True)

    assert _hp(repos, hero) == before


def test_friends_do_not_swing_at_each_other(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, _hero, goblin, _archer = fight
    # The goblin walks away from the other goblin's neighbour -- the hero is
    # the only thing adjacent, and a goblin is no enemy of a goblin.
    repos.encounters.place(tokens["hero"].id, 6, 6)
    repos.encounters.place(tokens["archer"].id, 2, 0)
    before = _hp(repos, goblin)

    server._do_move(tokens["goblin"].id, 1, 5, spending=True)

    assert _hp(repos, goblin) == before


# ---------------------------------------------------------------- one a round


def test_a_reaction_is_spent_once_a_round(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5, 19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight

    server._do_move(tokens["hero"].id, 0, 4, spending=True)
    hurt_once = _hp(repos, hero)

    # Walk back into reach and out again in the same round.
    server._do_move(tokens["hero"].id, 0, 0, spending=True)
    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert _hp(repos, hero) == hurt_once, "the goblin reacted twice in one round"


def test_the_reaction_comes_back_next_round(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5, 19, 5))
    server, repos, encounter, tokens, hero, _goblin, _archer = fight

    server._do_move(tokens["hero"].id, 0, 4, spending=True)
    hurt_once = _hp(repos, hero)

    # Round the order, twice, so the round number moves on.
    for _ in range(4):
        server.run_turn("next")
    server._do_move(tokens["hero"].id, 0, 0, spending=True)
    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert _hp(repos, hero) < hurt_once, "the reaction never came back"


# --------------------------------------------------------------- falling over


def test_dropped_on_the_way_out_you_never_arrive(fight, monkeypatch):
    """You fall where you stood, which is where help will come looking."""
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    entity = repos.entities.get(hero.id)
    data = dict(entity.data)
    data["hp"] = 1
    entity.data = data
    repos.entities.update(entity)
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 6))

    moved = server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert moved is False
    assert _hp(repos, hero) == 0


def test_the_unconscious_do_not_swing_at_passers_by(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, goblin, _archer = fight
    entity = repos.entities.get(goblin.id)
    data = dict(entity.data)
    data["hp"] = 0
    entity.data = data
    repos.entities.update(entity)
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert _hp(repos, hero) == before


# ----------------------------------------------------------------- the rule alone


def test_only_melee_holds_ground(content):
    assert attack.threatens(_sheet(["battleaxe"]), content, 1) is True
    assert attack.threatens(_sheet(["shortbow"]), content, 1) is False
    assert attack.threatens(_sheet(["battleaxe"]), content, 2) is False
    assert attack.threatens(_sheet(["chain-mail"]), content, 1) is False


def test_the_weapon_taken_up_is_the_first_melee_one(content):
    weapon = attack.melee_weapon(_sheet(["shortbow", "battleaxe"]), content)
    assert weapon is not None and weapon.index == "battleaxe"
    assert attack.melee_weapon(_sheet(["shortbow"]), content) is None
