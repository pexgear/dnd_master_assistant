"""Whose turn it is, and who is allowed to end it.

The order is the spine of a fight, and the ways it goes wrong are quiet: a
player passed over sees nothing at all, and neither does anybody else. So this
walks a whole round and counts, rather than trusting that advancing works
because each piece of it does.

The rule the last of these guards is the one that actually bit: **autopilot may
end its own turns and nobody else's.** It is *told* to call next_turn once it
has resolved whoever was up, and being told is not a rule -- a model that called
it one turn early took a person's turn away with nothing on screen to explain
it.
"""
from __future__ import annotations

import pytest

from canon_keeper.net.server import SessionServer
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity


@pytest.fixture
def table(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Turns")
    hero = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok",
               data={"hp": 28, "max_hp": 28, "sheet": {"schema": 1, "level": 3}}))
    mate = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Marla",
               data={"hp": 22, "max_hp": 22, "sheet": {"schema": 1, "level": 3}}))
    goblin = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Yeemik",
               data={"hp": 7, "max_hp": 7, "sheet": {"schema": 1, "level": 1}}))

    marco = repos.accounts.create(campaign.id, "marco", "goblin-teeth",
                                  character_entity_id=hero.id)
    elsa = repos.accounts.create(campaign.id, "elsa", "goblin-teeth",
                                 character_entity_id=mate.id)
    repos.entities.set_owner(hero.id, marco.id)
    repos.entities.set_owner(mate.id, elsa.id)

    enc = repos.encounters.create(campaign.id, "The cave", width=12, height=12)
    tokens = {
        "hero": repos.encounters.add(enc.id, hero.id, initiative=20, x=0, y=0),
        "mate": repos.encounters.add(enc.id, mate.id, initiative=15, x=1, y=0),
        "goblin": repos.encounters.add(enc.id, goblin.id, initiative=10, x=2, y=0),
    }
    repos.encounters.begin(enc.id)
    server = SessionServer(repos, campaign.id, "Turns")
    assert server.start(0, announce=False)
    yield server, repos, enc, tokens, hero, mate, goblin
    server.stop()


def test_a_round_visits_everybody_once(table):
    server, repos, enc, tokens, *_ = table
    seen = [repos.encounters.get(enc.id).turn_combatant_id]
    for _ in range(2):
        server.run_turn("next")
        seen.append(repos.encounters.get(enc.id).turn_combatant_id)
    assert seen == [tokens["hero"].id, tokens["mate"].id, tokens["goblin"].id]

    server.run_turn("next")
    assert repos.encounters.get(enc.id).turn_combatant_id == tokens["hero"].id
    assert repos.encounters.get(enc.id).round == 2


def test_a_played_character_is_not_machine_played(table):
    server, repos, enc, tokens, *_ = table
    for key in ("hero", "mate"):
        c = repos.encounters.combatant(tokens[key].id)
        assert server._machine_plays(c) is False, f"{key} would be played by autopilot"
    goblin = repos.encounters.combatant(tokens["goblin"].id)
    assert server._machine_plays(goblin) is True


def test_nobody_alive_is_ever_passed_over(table):
    server, repos, enc, tokens, *_ = table
    assert server._resting() == frozenset()


def test_autopilot_cannot_pass_a_players_turn(qtbot, table):
    """The agent resolving a monster must not be able to skip the person next.

    It is told to call next_turn once it has resolved whoever was up. Nothing
    made it true, and a model that calls it one turn early takes somebody's
    turn away without anybody seeing why.
    """
    import canon_keeper.net.client as client_module
    from canon_keeper_protocol import MessageType

    server, repos, enc, tokens, *_ = table
    server.set_autopilot(True, by="The DM")
    repos.accounts.create(
        server.campaign_id, "autopilot", "let-me-run-it", role="agent",
        display_name="Autopilot",
    )

    agent = client_module.SessionClient()
    try:
        agent.join(f"ws://127.0.0.1:{server.port}", "autopilot", "let-me-run-it")
        qtbot.waitUntil(lambda: agent.me is not None, timeout=10000)

        assert repos.encounters.get(enc.id).turn_combatant_id == tokens["hero"].id
        agent._send(MessageType.TURN, action="next")
        qtbot.wait(400)

        assert repos.encounters.get(enc.id).turn_combatant_id == tokens["hero"].id, (
            "autopilot passed a turn belonging to somebody who plays for themselves"
        )
    finally:
        agent.leave()
