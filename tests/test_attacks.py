"""Taking a turn: the arithmetic, and the three parties who have to agree.

A player says "I get behind the orc and hit it with my axe". Three things then
happen, and none of them can skip the others.

* The **agent** turns words into rules and *proposes*. It never moves anybody.
* The **host** decides whether that is even legal, and does all the rolling.
* The **player** owns their own turn. Nothing touches their character until
  they accept, and refusing costs one click.

That last one is the difference between an app that offers to move your
character into a fire and one that does it.
"""

from __future__ import annotations

import pytest

from canon_keeper.content import Content
from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper.rules import attack
from canon_keeper_protocol import MessageType


def _sheet(**overrides) -> dict:
    sheet = {
        "schema": 1,
        "species": "human",
        "class_index": "fighter",
        "level": 3,
        "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 10},
        "equipment": ["battleaxe", "shortbow", "chain-mail"],
        "hp_current": 28,
    }
    sheet.update(overrides)
    return sheet


@pytest.fixture
def content(repos) -> Content:
    return Content(repos.settings)


class _Rolls:
    """Dice the test decides, in the shape the host's roller returns."""

    def __init__(self, *totals: int) -> None:
        self._totals = list(totals)

    def __call__(self, notation: str):
        value = self._totals.pop(0) if self._totals else 1

        class _Result:
            total = value
            rolls = [value]

        return _Result()


# ------------------------------------------------------------------ the weapon


def test_a_weapon_is_read_off_the_sheet(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    assert weapon.name == "Battleaxe"
    assert weapon.dice == "1d8"
    assert weapon.ranged is False


def test_a_loose_name_still_finds_it(content):
    """It comes out of a sentence somebody typed, not a dropdown."""
    assert attack.find_weapon(_sheet(), content, "axe").index == "battleaxe"
    assert attack.find_weapon(_sheet(), content, "Bow").index == "shortbow"


def test_asking_for_something_they_do_not_have_says_what_they_do(content):
    with pytest.raises(attack.NoAttack) as raised:
        attack.find_weapon(_sheet(), content, "greatsword")
    assert "Battleaxe" in str(raised.value)


def test_no_weapon_at_all_is_a_refusal_not_a_crash(content):
    with pytest.raises(attack.NoAttack):
        attack.find_weapon(_sheet(equipment=["chain-mail"]), content, "axe")


def test_armour_and_rope_are_not_weapons(content):
    carried = {w.index for w in attack.weapons_of(_sheet(), content)}
    assert carried == {"battleaxe", "shortbow"}


# ------------------------------------------------------------------ the numbers


def test_a_melee_attack_uses_strength(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    # +3 strength, +2 proficiency at level 3.
    assert attack.attack_bonus(_sheet(), content, weapon) == 5


def test_a_ranged_attack_uses_dexterity(content):
    weapon = attack.find_weapon(_sheet(), content, "shortbow")
    assert attack.attack_bonus(_sheet(), content, weapon) == 4


def test_a_finesse_weapon_takes_the_better_of_the_two(content):
    sheet = _sheet(
        equipment=["dagger"],
        abilities={"str": 8, "dex": 18, "con": 12, "int": 10, "wis": 10, "cha": 10},
    )
    weapon = attack.find_weapon(sheet, content, "dagger")
    assert attack.attack_bonus(sheet, content, weapon) == 6, "dexterity, not strength"


def test_reach_is_one_square_including_diagonals(content):
    axe = attack.find_weapon(_sheet(), content, "battleaxe")
    assert attack.within_reach(axe, 1)
    assert not attack.within_reach(axe, 2)

    bow = attack.find_weapon(_sheet(), content, "shortbow")
    assert attack.within_reach(bow, 10)


def test_distance_is_measured_the_way_a_table_measures_it():
    assert attack.squares_between((0, 0), (1, 1)) == 1
    assert attack.squares_between((0, 0), (3, -1)) == 3


# ------------------------------------------------------------------ the swing


def test_a_hit_takes_the_modifier_and_the_dice(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    result = attack.resolve(_sheet(), content, weapon, 13, _Rolls(12, 5))

    assert result.total == 17
    assert result.hit
    assert result.damage == 8, "5 on the die plus 3 strength"


def test_a_miss_rolls_no_damage(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    result = attack.resolve(_sheet(), content, weapon, 18, _Rolls(4))
    assert not result.hit
    assert result.damage == 0


def test_a_natural_twenty_always_hits(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    result = attack.resolve(_sheet(), content, weapon, 40, _Rolls(20, 6))
    assert result.hit
    assert result.critical


def test_a_natural_one_always_misses(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    result = attack.resolve(_sheet(), content, weapon, 2, _Rolls(1))
    assert not result.hit


def test_a_critical_doubles_the_dice_and_not_the_modifier(content):
    """The rule people most often get wrong in the generous direction."""
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    rolled: list[str] = []

    def watching(notation):
        rolled.append(notation)
        return _Rolls(20 if len(rolled) == 1 else 9)(notation)

    result = attack.resolve(_sheet(), content, weapon, 10, watching)
    assert rolled[1] == "2d8"
    assert result.damage == 12, "9 on the dice plus 3, not plus 6"


def test_damage_is_never_less_than_one(content):
    weak = _sheet(abilities={"str": 1, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10})
    weapon = attack.find_weapon(weak, content, "battleaxe")
    result = attack.resolve(weak, content, weapon, 1, _Rolls(15, 1))
    assert result.hit
    assert result.damage == 1


def test_it_reads_as_a_sentence(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    said = attack.resolve(_sheet(), content, weapon, 13, _Rolls(12, 5)).describe(
        "Brok", "Yeemik"
    )
    assert "Brok attacks Yeemik with Battleaxe" in said
    assert "AC 13" in said
    assert "hit for 8" in said


# ------------------------------------------------------- the whole thing, live


@pytest.fixture
def fight(qtbot, repos):
    """A player with an axe, a goblin, and a fight already running."""
    campaign = repos.campaigns.ensure_default("Combat")
    hero = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Brok",
            data={"hp": 28, "max_hp": 28, "sheet": _sheet()},
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
                "sheet": {
                    "schema": 1,
                    "level": 1,
                    "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10,
                                  "wis": 8, "cha": 8},
                    "overrides": {"ac": 10, "hp_max": 12},
                },
            },
        )
    )
    marco = repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", display_name="Marco",
        character_entity_id=hero.id,
    )
    repos.accounts.create(
        campaign.id, "autopilot", "let-me-run-it", role="agent", display_name="Autopilot"
    )
    repos.entities.set_owner(hero.id, marco.id)
    repos.shares.share(campaign.id, goblin.id)

    encounter = repos.encounters.create(campaign.id, "The cave", width=10, height=10)
    tokens = {
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=18, x=-3, y=0),
        "goblin": repos.encounters.add(encounter.id, goblin.id, initiative=9, x=1, y=0),
    }
    repos.encounters.begin(encounter.id)

    server = SessionServer(repos, campaign.id, "Combat session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    server.set_autopilot(True, by="The DM")
    yield server, repos, encounter, tokens, hero, goblin
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def _propose(agent, tokens, **overrides) -> None:
    payload = {
        "combatant": tokens["hero"].id,
        "move": [0, 0],
        "target": tokens["goblin"].id,
        "weapon": "battleaxe",
        "text": "Move to 0,0 and attack Yeemik with a battleaxe.",
    }
    payload.update(overrides)
    agent._send(MessageType.PROPOSE, **payload)


def test_a_proposal_reaches_the_player_whose_turn_it_is(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        action = offered[0]
        assert action["who"] == "Brok"
        assert action["move"] == [0, 0]
        assert action["target_name"] == "Yeemik"
        assert "battleaxe" in action["text"]
    finally:
        agent.leave()
        player.leave()


def test_nothing_happens_until_they_accept(qtbot, fight):
    """The whole point. A proposal is not a move."""
    server, repos, _encounter, tokens, _hero, goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        qtbot.wait(200)

        assert repos.encounters.combatant(tokens["hero"].id).x == -3
        assert repos.entities.get(goblin.id).data["hp"] == 12
    finally:
        agent.leave()
        player.leave()


def test_accepting_moves_and_swings(qtbot, fight):
    server, repos, _encounter, tokens, _hero, goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        player.send_answer(offered[0]["id"], True)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["hero"].id).x == 0, timeout=5000
        )
        # AC 10 against +5 to hit: it lands far more often than not, and either
        # way the swing happened and is in the log.
        qtbot.waitUntil(
            lambda: any(
                "attacks Yeemik" in m.get("text", "") for m in server.history()
            ),
            timeout=5000,
        )
    finally:
        agent.leave()
        player.leave()


def test_refusing_leaves_everything_alone(qtbot, fight):
    server, repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        player.send_answer(offered[0]["id"], False, "I stay where I am and throw a rock")
        qtbot.wait(400)

        assert repos.encounters.combatant(tokens["hero"].id).x == -3
        assert any(
            "throw a rock" in m.get("text", "") for m in server.history()
        ), "what they said instead has to reach the agent"
    finally:
        agent.leave()
        player.leave()


def test_only_the_player_whose_turn_it_is_may_answer(qtbot, fight):
    """Somebody else accepting your turn is somebody else playing your character."""
    server, repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        # The agent is not the player, and holds a DM-shaped view. It still
        # cannot answer for them.
        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent.send_answer(offered[0]["id"], True)
        qtbot.wait(200)
        assert repos.encounters.combatant(tokens["hero"].id).x == -3
    finally:
        agent.leave()
        player.leave()


def test_a_player_cannot_propose_their_own_turn(qtbot, fight):
    """Otherwise the confirmation is a client asking itself."""
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.failed, timeout=5000):
            _propose(player, tokens)
    finally:
        player.leave()


def test_an_impossible_square_is_refused_before_anyone_is_asked(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        with qtbot.waitSignal(agent.failed, timeout=5000) as blocker:
            _propose(agent, tokens, move=[99, 99])
        assert "off the map" in " ".join(str(a) for a in blocker.args)
        qtbot.wait(200)
        assert offered == [], "a player must never be shown an illegal turn"
    finally:
        agent.leave()
        player.leave()


def test_a_square_somebody_is_standing_on_is_refused(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        with qtbot.waitSignal(agent.failed, timeout=5000) as blocker:
            _propose(agent, tokens, move=[1, 0])
        assert "already at" in " ".join(str(a) for a in blocker.args)
    finally:
        agent.leave()


def test_nobody_attacks_themselves(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        with qtbot.waitSignal(agent.failed, timeout=5000):
            _propose(agent, tokens, target=tokens["hero"].id)
    finally:
        agent.leave()


def test_a_second_proposal_takes_the_first_one_back(qtbot, fight):
    """A player answering a stale offer would be acting on an older map."""
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    withdrawn: list[str] = []
    player.action_withdrawn.connect(withdrawn.append)
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        first = offered[0]["id"]

        _propose(agent, tokens, move=[-1, 0], text="Move to -1,0 instead.")
        qtbot.waitUntil(lambda: first in withdrawn, timeout=5000)
    finally:
        agent.leave()
        player.leave()


# --------------------------------------------------- the chat box, in place of Send
#
# The confirmation is not a dialog somewhere else. It is where Send was, and it
# holds the box: there are exactly three ways past it -- do it, say more, or
# refuse -- and none of them is carrying on chatting and never coming back.


@pytest.fixture
def chat(qtbot, repos):
    """A player's Table panel, with nothing connected."""
    import logging

    from canon_keeper.bus import Bus
    from canon_keeper.net.state import SharedState
    from canon_keeper.panels.table.widget import TableWidget
    from canon_keeper.plugin import AppContext

    campaign = repos.campaigns.ensure_default("Turn")
    ctx = AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=campaign.id,
        role="player",
        shared=SharedState(),
    )
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    return widget, ctx


def _offered(**overrides) -> dict:
    action = {
        "id": "abc123",
        "combatant": 11,
        "who": "Brok",
        "move": [0, 0],
        "target": 12,
        "target_name": "Yeemik",
        "weapon": "battleaxe",
        "text": "Move to 0,0 and attack Yeemik with a battleaxe.",
    }
    action.update(overrides)
    return action


def test_the_offer_replaces_sending(chat):
    widget, _ctx = chat
    widget._on_turn_offered(_offered())

    assert not widget._offer_bar.isHidden()
    assert "battleaxe" in widget._offer_label.text()
    assert not widget._entry.isEnabled(), "you cannot carry on chatting"


def test_doing_it_answers_and_gives_the_box_back(chat, qtbot):
    widget, ctx = chat
    widget._on_turn_offered(_offered())

    with qtbot.waitSignal(ctx.bus.action_answered, timeout=1000) as blocker:
        widget._accept_turn()

    assert blocker.args == ["abc123", True, ""]
    assert widget._offer_bar.isHidden()


def test_refusing_answers_and_gives_the_box_back(chat, qtbot):
    widget, ctx = chat
    widget._on_turn_offered(_offered())

    with qtbot.waitSignal(ctx.bus.action_answered, timeout=1000) as blocker:
        widget._refuse_turn()

    assert blocker.args == ["abc123", False, ""]
    assert widget._offer_bar.isHidden()


def test_saying_more_unlocks_the_box_without_answering(chat):
    """The offer stands while you explain -- the map is still showing it."""
    widget, _ctx = chat
    widget._on_turn_offered(_offered())

    widget._unlock_to_say_more()

    assert widget._offer_bar.isHidden()
    assert widget._offered is not None, "not answered, only being corrected"
    assert "What did you mean" in widget._entry.placeholderText()


def test_the_offer_comes_back_after_one_more_message(chat, monkeypatch):
    """One message, then it waits again rather than leaving the turn open."""
    widget, _ctx = chat
    widget._on_turn_offered(_offered())
    widget._unlock_to_say_more()

    monkeypatch.setattr(widget._client, "send_chat", lambda _text: True)
    widget._entry.setText("no, I stay put and shoot it")
    widget._send()

    assert not widget._offer_bar.isHidden()


def test_the_dms_copy_does_not_hold_their_box(chat):
    widget, _ctx = chat
    widget._on_turn_offered(_offered(watching=True))

    assert widget._offer_bar.isHidden()
    assert widget._offered is None


def test_a_withdrawn_offer_gives_the_box_back(chat):
    widget, _ctx = chat
    widget._on_turn_offered(_offered())
    widget._on_turn_withdrawn("abc123")

    assert widget._offer_bar.isHidden()
    assert widget._offered is None


def test_your_turn_is_said_in_the_chat(chat):
    """The map has already said it, in a colour, on a fingernail."""
    widget, _ctx = chat
    widget._ctx.shared.replace_all(
        [{"id": 7, "kind": "pc", "name": "Brok", "data": {}, "own": True}]
    )
    widget._on_encounter_received(
        {"turn": 11, "combatants": [{"id": 11, "entity": 7, "x": 0, "y": 0}]}
    )

    assert any(kind == "turn" for kind, _text, _at in widget._entries)


def test_it_is_said_once(chat):
    widget, _ctx = chat
    widget._ctx.shared.replace_all(
        [{"id": 7, "kind": "pc", "name": "Brok", "data": {}, "own": True}]
    )
    fight = {"turn": 11, "combatants": [{"id": 11, "entity": 7, "x": 0, "y": 0}]}
    widget._on_encounter_received(fight)
    widget._on_encounter_received(fight)

    assert sum(1 for kind, _t, _a in widget._entries if kind == "turn") == 1


def test_somebody_elses_turn_says_nothing(chat):
    widget, _ctx = chat
    widget._ctx.shared.replace_all(
        [{"id": 7, "kind": "pc", "name": "Brok", "data": {}, "own": True}]
    )
    widget._on_encounter_received(
        {"turn": 99, "combatants": [{"id": 11, "entity": 7, "x": 0, "y": 0}]}
    )

    assert not any(kind == "turn" for kind, _t, _a in widget._entries)


def test_swinging_from_across_the_room_is_refused(qtbot, fight):
    """Melee reaches one square. Accepting must not quietly teleport the axe."""
    server, repos, _encounter, tokens, _hero, goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens, move=None, text="Attack Yeemik from here.")
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        player.send_answer(offered[0]["id"], True)
        qtbot.waitUntil(
            lambda: any("too far" in m.get("text", "") for m in server.history()),
            timeout=5000,
        )
        assert repos.entities.get(goblin.id).data["hp"] == 12
    finally:
        agent.leave()
        player.leave()
