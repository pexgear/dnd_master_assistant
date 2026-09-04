"""The one roll nobody chooses to make, put to the person who owes it.

Death saves were correct and invisible: the host rolled them the instant the
turn came round, and the player watched a number change in a list. That is the
loudest moment the game has, happening off-screen.

So the **rule** asks. Not autopilot -- the host, because this is not a ruling
anybody makes, it is what the book says happens at the start of your turn at
zero hit points. It goes into the chat where everyone can see it asked, which
is most of what makes a death save what it is, and where the person who owes it
can press it.

**Forced.** They may take their time and they may ignore it; when the clock
runs out the host rolls it for them and says so. There is no answer that makes
it not happen, because there is no such answer at a table. And with nobody
there to ask -- an unowned character, or one a machine is playing -- it is
rolled at once, exactly as it always was.

The rules themselves are unchanged and live in `canon_keeper.rules.death`:
ten or better on a bare d20, no modifier of any kind, three either way, a
natural twenty stands you back up on one hit point and a natural one costs two.
"""

from __future__ import annotations

import pytest

import canon_keeper.net.server as server_module
from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper.panels.table import rolls
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper.rules import death


class _Rolls:
    def __init__(self, *totals: int) -> None:
        self._totals = list(totals)

    def __call__(self, notation: str):
        value = self._totals.pop(0) if self._totals else 12

        class _Result:
            total = value
            rolls = [value]

        return _Result()


# ------------------------------------------------------- reading it in a line


def test_the_chat_line_becomes_a_roll_you_can_press():
    found = rolls.find("Brok is dying, and owes a death saving throw.")

    assert [p.kind for p in found] == [rolls.DEATH]
    assert found[0].text == "death saving throw"


def test_the_short_way_of_saying_it_counts_too():
    assert [p.kind for p in rolls.find("make a death save")] == [rolls.DEATH]


def test_the_dc_is_the_rule_rather_than_something_written_down():
    """Nobody writes "DC 10" because nobody chose it."""
    assert rolls.find("a death saving throw")[0].dc == death.DEATH_SAVE_DC


def test_it_takes_no_modifier_at_all(content=None):
    """Not proficiency, not Constitution. That is what makes it frightening."""
    prompt = rolls.find("death save")[0]
    fat_sheet = {"schema": 1, "level": 12, "abilities": {"con": 20}}

    assert rolls.bonus_for(prompt, fat_sheet, None) == 0


def test_an_ordinary_save_is_still_an_ordinary_save():
    """The new pattern must not swallow "Dexterity saving throw"."""
    assert [p.kind for p in rolls.find("a Dexterity saving throw")] == [rolls.SAVE]


# --------------------------------------------------------------- being asked


@pytest.fixture
def fight(qtbot, repos):
    """Brok is Marco's, is on zero hit points, and it is about to be his turn."""
    campaign = repos.campaigns.ensure_default("Dying")
    marco = repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    brok = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok",
               data={"hp": 0, "max_hp": 28, "sheet": {"schema": 1, "level": 3}})
    )
    goblin = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Yeemik",
               data={"hp": 7, "max_hp": 7, "sheet": {"schema": 1, "level": 1}})
    )
    repos.entities.set_owner(brok.id, marco.id)
    repos.accounts.set_character(marco.id, brok.id)

    encounter = repos.encounters.create(campaign.id, "The cave", width=12, height=12)
    tokens = {
        # The goblin goes first, so that one pass of the turn lands on Brok --
        # which is the moment the save is owed.
        "brok": repos.encounters.add(encounter.id, brok.id, initiative=10, x=0, y=0),
        "goblin": repos.encounters.add(encounter.id, goblin.id, initiative=20, x=2, y=0),
    }
    repos.encounters.begin(encounter.id)

    server = SessionServer(repos, campaign.id, "Dying session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, repos, encounter, tokens, brok, goblin
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def _said(server) -> list[str]:
    return [line.get("text", "") for line in server.history()]


def _to_broks_turn(server) -> None:
    """Pass the goblin's turn, which hands the next one to Brok."""
    server.run_turn("next")


def test_the_save_is_asked_for_rather_than_taken(qtbot, fight, monkeypatch):
    server, repos, _encounter, tokens, _brok, _goblin = fight
    monkeypatch.setattr(server_module, "roll", _Rolls(15))
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        _to_broks_turn(server)
        qtbot.wait(300)

        assert any("owes a death saving throw" in line for line in _said(server))
        after = repos.encounters.combatant(tokens["brok"].id)
        assert (after.death_successes, after.death_failures) == (0, 0), (
            "the host rolled it instead of asking"
        )
    finally:
        marco.leave()


def test_the_turn_waits_on_them(qtbot, fight, monkeypatch):
    """The save *is* the turn, so it does not move on until it is answered."""
    server, repos, encounter, tokens, *_ = fight
    monkeypatch.setattr(server_module, "roll", _Rolls(15))
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        _to_broks_turn(server)
        qtbot.wait(300)

        assert repos.encounters.get(encounter.id).turn_combatant_id == tokens["brok"].id
    finally:
        marco.leave()


def test_rolling_it_records_it_and_moves_on(qtbot, fight, monkeypatch):
    server, repos, encounter, tokens, *_ = fight
    monkeypatch.setattr(server_module, "roll", _Rolls(15))
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        _to_broks_turn(server)
        qtbot.wait(300)

        marco.send_death_save()
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["brok"].id).death_successes == 1,
            timeout=5000,
        )
        assert repos.encounters.get(encounter.id).turn_combatant_id != tokens["brok"].id
    finally:
        marco.leave()


def test_a_natural_twenty_stands_them_up_and_the_turn_is_theirs(qtbot, fight, monkeypatch):
    server, repos, encounter, tokens, brok, _goblin = fight
    monkeypatch.setattr(server_module, "roll", _Rolls(20))
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        _to_broks_turn(server)
        qtbot.wait(300)
        marco.send_death_save()

        qtbot.waitUntil(
            lambda: repos.entities.get(brok.id).data["hp"] == 1, timeout=5000
        )
        assert repos.encounters.get(encounter.id).turn_combatant_id == tokens["brok"].id
    finally:
        marco.leave()


# ------------------------------------------------------------------- forced


def test_ignoring_it_does_not_make_it_go_away(qtbot, fight, monkeypatch):
    """The clock is what makes it forced. Somebody rolls it; it may as well be us."""
    server, repos, _encounter, tokens, *_ = fight
    monkeypatch.setattr(server_module, "roll", _Rolls(15))
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        _to_broks_turn(server)
        qtbot.wait(300)

        server._nobody_answered()  # what the clock does when it runs out

        after = repos.encounters.combatant(tokens["brok"].id)
        assert after.death_successes == 1, "the save never happened"
        assert any("does not answer" in line for line in _said(server))
    finally:
        marco.leave()


def test_with_nobody_there_it_is_rolled_at_once(qtbot, fight, monkeypatch):
    """Unchanged where there is nobody to ask. The rule happens either way."""
    server, repos, _encounter, tokens, *_ = fight
    monkeypatch.setattr(server_module, "roll", _Rolls(15))

    _to_broks_turn(server)

    after = repos.encounters.combatant(tokens["brok"].id)
    assert after.death_successes == 1


def test_a_machine_playing_them_is_not_asked(qtbot, fight, monkeypatch):
    """Nothing to press a button, so nothing waits for one to be pressed."""
    server, repos, _encounter, tokens, *_ = fight
    monkeypatch.setattr(server_module, "roll", _Rolls(15))
    repos.encounters.set_simulated(tokens["brok"].id, True)
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        _to_broks_turn(server)
        qtbot.wait(300)

        assert repos.encounters.combatant(tokens["brok"].id).death_successes == 1
    finally:
        marco.leave()


# ------------------------------------------------------------- whose save


def test_nobody_can_roll_a_save_that_is_not_owed(qtbot, fight, monkeypatch):
    """Otherwise a client could roll death saves into existence."""
    server, repos, _encounter, tokens, *_ = fight
    monkeypatch.setattr(server_module, "roll", _Rolls(15))
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(marco.failed, timeout=5000):
            marco.send_death_save()
        after = repos.encounters.combatant(tokens["brok"].id)
        assert (after.death_successes, after.death_failures) == (0, 0)
    finally:
        marco.leave()


def test_somebody_elses_save_is_not_yours_to_roll(qtbot, fight, monkeypatch):
    server, repos, _encounter, tokens, *_ = fight
    monkeypatch.setattr(server_module, "roll", _Rolls(15))
    campaign = repos.campaigns.ensure_default("Dying")
    repos.accounts.create(campaign.id, "elsa", "goblin-teeth")
    marco = _join(qtbot, server, "marco", "goblin-teeth")
    elsa = _join(qtbot, server, "elsa", "goblin-teeth")
    try:
        _to_broks_turn(server)
        qtbot.wait(300)

        with qtbot.waitSignal(elsa.failed, timeout=5000):
            elsa.send_death_save()
        after = repos.encounters.combatant(tokens["brok"].id)
        assert (after.death_successes, after.death_failures) == (0, 0)
    finally:
        elsa.leave()
        marco.leave()
