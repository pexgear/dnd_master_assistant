"""Autopilot: handing the table to an agent, and taking it back.

The DM is normally a human, and Canon Keeper exists for that human. Autopilot is
a switch they hold: while it is on an agent answers in their place, and the
moment it is off the agent goes quiet. That "goes quiet" is the thing worth
testing, because it is what makes handing over safe -- it is enforced by the
host refusing the agent's chat, not by the agent choosing to stop.
"""

from __future__ import annotations

import pytest

from canon_keeper.net.client import SessionClient
from canon_keeper.net.projection import Viewer, project_facts
from canon_keeper.net.server import SessionServer
from canon_keeper_protocol import MessageType, Role


@pytest.fixture
def server(ctx) -> SessionServer:
    return SessionServer(ctx.repos, ctx.campaign_id, "Test Table")


@pytest.fixture
def agent_account(ctx):
    return ctx.repos.accounts.create(
        ctx.campaign_id, "autopilot", "hunter2hunter2", role="agent"
    )


# ------------------------------------------------------------------ the login


def test_an_agent_account_can_exist(agent_account):
    assert agent_account.role == "agent"
    assert agent_account.is_agent is True


def test_an_agent_is_not_a_dm(agent_account):
    """It stands in for one. It is not one, and the difference is its authority."""
    assert agent_account.is_dm is False


def test_but_it_sees_what_a_dm_sees(agent_account):
    """It answers from the canon, so it needs the canon."""
    assert agent_account.sees_everything is True


def test_an_ordinary_player_sees_nothing_extra(ctx):
    player = ctx.repos.accounts.create(ctx.campaign_id, "marco", "hunter2hunter2")
    assert player.sees_everything is False


def test_an_unknown_role_falls_back_to_player(ctx):
    """A typo must not mint authority."""
    account = ctx.repos.accounts.create(
        ctx.campaign_id, "typo", "hunter2hunter2", role="admin"
    )
    assert account.role == "player"


def test_the_campaign_knows_whether_it_has_an_agent(server, ctx, agent_account):
    assert server.has_agent is True


def test_a_campaign_without_one_says_so(server, ctx):
    ctx.repos.accounts.create(ctx.campaign_id, "marco", "hunter2hunter2")
    assert server.has_agent is False


# ------------------------------------------------------------------ the switch


def test_autopilot_starts_off(server):
    """Opening a campaign must never find a machine already running your table."""
    assert server.autopilot is False


def test_it_turns_on_and_off(server):
    server.set_autopilot(True, by="Genna")
    assert server.autopilot is True

    server.set_autopilot(False, by="Genna")
    assert server.autopilot is False


def test_turning_it_on_is_said_out_loud(server):
    server.set_autopilot(True, by="Genna")

    said = [m["text"] for m in server.history()]
    assert any("autopilot" in text.lower() for text in said)


def test_turning_it_off_is_said_too(server):
    server.set_autopilot(True, by="Genna")
    server.set_autopilot(False, by="Genna")

    said = [m["text"] for m in server.history()]
    assert sum("autopilot" in text.lower() for text in said) >= 2


def test_setting_it_to_what_it_already_is_says_nothing(server):
    """Otherwise a redundant click narrates itself to the whole table."""
    server.set_autopilot(True, by="Genna")
    before = len(server.history())

    server.set_autopilot(True, by="Genna")

    assert len(server.history()) == before


def test_it_is_not_remembered_between_sessions(ctx, agent_account):
    """A fresh server is a fresh decision."""
    first = SessionServer(ctx.repos, ctx.campaign_id, "Table")
    first.set_autopilot(True, by="Genna")

    second = SessionServer(ctx.repos, ctx.campaign_id, "Table")

    assert second.autopilot is False


# ------------------------------------------------------------------- the canon


def test_the_agent_receives_the_canon(ctx, agent_account):
    """Its whole job is answering from what is true."""
    viewer = Viewer(account_id=agent_account.id, is_dm=agent_account.sees_everything)
    ctx.repos.facts.assert_fact(ctx.campaign_id, None, "the town of", "Phandalin")

    assert len(project_facts(ctx.repos, ctx.campaign_id, viewer)) == 1


# ------------------------------------------------------------------ the wire


def test_the_roster_names_the_agent_as_one():
    """Not disguised as a DM. A table deserves to know."""
    assert Role.AGENT == "agent"
    assert Role.AGENT != Role.DM


def test_the_autopilot_message_exists():
    assert MessageType.AUTOPILOT == "autopilot"


# ------------------------------------------------- the gate, over a real socket
#
# The tests above check the flag. These check the only thing that matters: that
# an agent holding a valid login, connected and receiving, physically cannot get
# a word onto the table while the switch is off.


@pytest.fixture
def live(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Autopilot Campaign")
    repos.accounts.create(campaign.id, "marco", "goblin-teeth", display_name="Marco")
    repos.accounts.create(
        campaign.id, "autopilot", "let-me-run-it", role="agent", display_name="Autopilot"
    )
    instance = SessionServer(repos, campaign.id, "Autopilot session")
    assert instance.start(0, announce=False), "could not bind an ephemeral port"
    yield instance
    instance.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def test_the_agent_is_refused_while_autopilot_is_off(qtbot, live):
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    try:
        assert live.autopilot is False
        with qtbot.waitSignal(agent.failed, timeout=5000) as blocker:
            agent.send_chat("The innkeeper looks up as you enter.")
        assert "autopilot" in " ".join(str(a) for a in blocker.args).lower()
    finally:
        agent.leave()


def test_nobody_hears_what_the_agent_tried_to_say(qtbot, live):
    """Refused means refused, not held back and delivered late."""
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    player = _join(qtbot, live, "marco", "goblin-teeth")
    heard: list[str] = []
    player.said.connect(lambda _member, text, _aside: heard.append(text))
    try:
        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent.send_chat("You are all suddenly holding swords.")
        qtbot.wait(200)
        assert heard == []
        assert not any(
            "swords" in m.get("text", "") for m in live.history()
        ), "a refused line must not reach the log either"
    finally:
        agent.leave()
        player.leave()


def test_with_autopilot_on_the_agent_is_heard(qtbot, live):
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    player = _join(qtbot, live, "marco", "goblin-teeth")
    try:
        live.set_autopilot(True, by="Genna")
        with qtbot.waitSignal(player.said, timeout=5000) as blocker:
            agent.send_chat("The innkeeper looks up as you enter.")
        member, text, aside = blocker.args
        assert text == "The innkeeper looks up as you enter."
        assert aside is False, "an agent speaking is the table's line, not an aside"
        assert member.role == "agent", "the table should see who is answering"
    finally:
        agent.leave()
        player.leave()


def test_taking_the_table_back_silences_it_mid_session(qtbot, live):
    """No handshake, no drain: the DM interrupting a machine is the point."""
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    try:
        live.set_autopilot(True, by="Genna")
        with qtbot.waitSignal(agent.said, timeout=5000):
            agent.send_chat("first line")

        live.set_autopilot(False, by="Genna")

        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent.send_chat("second line")
    finally:
        agent.leave()


def test_a_player_cannot_turn_autopilot_on(qtbot, live):
    """The switch is the DM's. It is not on the wire for a client to flip."""
    player = _join(qtbot, live, "marco", "goblin-teeth")
    try:
        assert not hasattr(player, "set_autopilot")
        assert live.autopilot is False
    finally:
        player.leave()


def test_everyone_is_told_when_it_changes(qtbot, live):
    player = _join(qtbot, live, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.autopilot_changed, timeout=5000) as blocker:
            live.set_autopilot(True, by="Genna")
        on, by = blocker.args
        assert on is True
        assert by == "Genna"
    finally:
        player.leave()


def test_the_agent_still_receives_while_off(qtbot, live, repos):
    """Silenced, not deafened -- otherwise switching on would need a reconnect."""
    agent = _join(qtbot, live, "autopilot", "let-me-run-it")
    player = _join(qtbot, live, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(agent.said, timeout=5000) as blocker:
            player.send_chat("We push open the door.")
        assert blocker.args[1] == "We push open the door."
    finally:
        agent.leave()
        player.leave()
