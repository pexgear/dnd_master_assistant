"""Showing that something is happening, and what it is costing.

Two problems with the same shape. The agent waits for a lull, then thinks for
several seconds, and in that time the table sees an empty screen -- which is
indistinguishable from a broken agent. And the DM, who is paying, had no way to
know what it had cost until the bill arrived.
"""

from __future__ import annotations

import pytest

from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper_dm_agent.brain import PRICES, Usage, price
from canon_keeper_protocol import MessageType


@pytest.fixture
def live(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Busy Campaign")
    repos.accounts.create(campaign.id, "marco", "goblin-teeth", display_name="Marco")
    repos.accounts.create(campaign.id, "elsa", "silver-moon", display_name="Elsa")
    repos.accounts.create(
        campaign.id, "gm", "run-the-game", role="dm", display_name="The DM"
    )
    repos.accounts.create(
        campaign.id, "autopilot", "let-me-run-it", role="agent", display_name="Autopilot"
    )
    server = SessionServer(repos, campaign.id, "Busy session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def _say_busy(client: SessionClient, on: bool) -> None:
    from canon_keeper_protocol import encode

    client._socket.sendTextMessage(encode(MessageType.BUSY, on=on))


# ------------------------------------------------------------------- who is busy


def test_the_table_is_told_when_someone_is_composing(qtbot, live):
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    player = _join(qtbot, live, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.busy_changed, timeout=5000) as blocker:
            _say_busy(agent, True)
        member, on = blocker.args
        assert on is True
        assert member.label == "Autopilot"
    finally:
        agent.leave()
        player.leave()


def test_and_when_they_stop(qtbot, live):
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    player = _join(qtbot, live, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.busy_changed, timeout=5000):
            _say_busy(agent, True)
        with qtbot.waitSignal(player.busy_changed, timeout=5000) as blocker:
            _say_busy(agent, False)
        assert blocker.args[1] is False
    finally:
        agent.leave()
        player.leave()


def test_saying_it_twice_is_not_announced_twice(qtbot, live):
    """A repeated 'still busy' should not flicker on everyone's screen."""
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    player = _join(qtbot, live, "marco", "goblin-teeth")
    seen: list = []
    player.busy_changed.connect(lambda member, on: seen.append(on))
    try:
        with qtbot.waitSignal(player.busy_changed, timeout=5000):
            _say_busy(agent, True)
        _say_busy(agent, True)
        qtbot.wait(300)
        assert seen == [True]
    finally:
        agent.leave()
        player.leave()


def test_a_player_may_also_say_they_are_typing(qtbot, live):
    """Nothing about this is agent-only -- the table wants it for people too."""
    marco = _join(qtbot, live, "marco", "goblin-teeth")
    elsa = _join(qtbot, live, "elsa", "silver-moon")
    try:
        with qtbot.waitSignal(elsa.busy_changed, timeout=5000) as blocker:
            _say_busy(marco, True)
        assert blocker.args[0].label == "Marco"
    finally:
        marco.leave()
        elsa.leave()


def test_leaving_while_busy_clears_it(qtbot, live):
    """Otherwise 'Autopilot is writing...' outlives the agent, forever."""
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    player = _join(qtbot, live, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.busy_changed, timeout=5000):
            _say_busy(agent, True)

        with qtbot.waitSignal(player.busy_changed, timeout=5000) as blocker:
            agent.leave()

        assert blocker.args[1] is False
    finally:
        player.leave()


# ------------------------------------------------------------------- the bill


def _report(client: SessionClient, **spend) -> None:
    from canon_keeper_protocol import encode

    client._socket.sendTextMessage(encode(MessageType.SPENT, **spend))


def test_the_dm_is_told_what_it_cost(qtbot, live):
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    dm = _join(qtbot, live, "gm", "run-the-game")
    try:
        with qtbot.waitSignal(dm.spend_changed, timeout=5000) as blocker:
            _report(agent, tokens_in=4000, tokens_out=300, dollars=0.0275, turns=3)
        spend = blocker.args[0]
        assert spend["turns"] == 3
        assert spend["dollars"] == pytest.approx(0.0275)
    finally:
        agent.leave()
        dm.leave()


def test_a_player_is_never_told(qtbot, live):
    """It is the DM's bill, and a running cost on a player's screen is noise."""
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    player = _join(qtbot, live, "marco", "goblin-teeth")
    seen: list = []
    player.spend_changed.connect(seen.append)
    try:
        _report(agent, tokens_in=4000, tokens_out=300, dollars=0.03, turns=1)
        qtbot.wait(400)
        assert seen == []
    finally:
        agent.leave()
        player.leave()


def test_a_player_cannot_invent_a_figure(qtbot, live):
    """Otherwise anyone at the table can put a number on the DM's screen."""
    player = _join(qtbot, live, "marco", "goblin-teeth")
    dm = _join(qtbot, live, "gm", "run-the-game")
    seen: list = []
    dm.spend_changed.connect(seen.append)
    try:
        with qtbot.waitSignal(player.failed, timeout=5000):
            _report(player, tokens_in=99999999, tokens_out=1, dollars=9999.0, turns=1)
        qtbot.wait(200)
        assert seen == []
    finally:
        player.leave()
        dm.leave()


def test_the_server_holds_the_latest_figure(qtbot, live):
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    dm = _join(qtbot, live, "gm", "run-the-game")
    try:
        with qtbot.waitSignal(dm.spend_changed, timeout=5000):
            _report(agent, tokens_in=10, tokens_out=2, dollars=0.01, turns=1)
        with qtbot.waitSignal(dm.spend_changed, timeout=5000):
            _report(agent, tokens_in=20, tokens_out=4, dollars=0.02, turns=2)

        assert live.spend["turns"] == 2
    finally:
        agent.leave()
        dm.leave()


# ------------------------------------------------------------------- the maths


def test_a_known_model_is_priced():
    # Opus 5: $5 in, $25 out per million.
    assert price("claude-opus-5", 1_000_000, 0) == pytest.approx(5.00)
    assert price("claude-opus-5", 0, 1_000_000) == pytest.approx(25.00)


def test_a_realistic_turn_costs_pennies():
    """Sanity: a table's worth of answering should not be alarming."""
    one_turn = price("claude-opus-5", 6_000, 250)
    assert 0 < one_turn < 0.05
    assert one_turn * 40 < 2.00, "a whole session should stay near a pound or two"


def test_an_unknown_model_reports_no_cost_rather_than_a_guess():
    """Prices change. Guessing from a neighbour would be worse than silence."""
    assert price("some-model-released-next-year", 1_000_000, 1_000_000) == 0.0


def test_every_offered_model_has_a_price():
    from canon_keeper.panels.table.agent_settings import MODELS

    for model_id, _label in MODELS:
        assert model_id in PRICES, f"{model_id} is offered but has no price"


def test_usage_adds_up():
    total = Usage(100, 10, 5, 0.01) + Usage(200, 20, 15, 0.02)
    assert total.input_tokens == 300
    assert total.output_tokens == 30
    assert total.cached_tokens == 20
    assert total.dollars == pytest.approx(0.03)
