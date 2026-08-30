"""The autopilot agent, against a real host.

These matter more than most tests here, because this is the first client that
is not the app. Anything both ends of the wire quietly assumed is visible for
the first time when a second implementation has to read it.

No model is called anywhere below. What is being checked is the plumbing --
login, what it learns, and above all what it is allowed to say -- and all of
that is decided by the host, not by the model.
"""

from __future__ import annotations

import asyncio

import pytest

from canon_keeper.net.server import SessionServer
from canon_keeper.repo.entities import KIND_NPC, Entity
from canon_keeper_dm_agent.context import build_prompt
from canon_keeper_client import AgentSession, LoginFailed, Table
from canon_keeper_protocol import Member


@pytest.fixture
def hosted(qtbot, repos):
    """A running session with a player and an agent login."""
    campaign = repos.campaigns.ensure_default("Phandalin")
    repos.accounts.create(campaign.id, "marco", "goblin-teeth", display_name="Marco")
    repos.accounts.create(
        campaign.id, "autopilot", "let-me-run-it", role="agent", display_name="Autopilot"
    )
    npc = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Toblen Stonehill",
            summary="Runs the inn.",
            data={"secrets": "He is hiding a Redbrand deserter in the cellar."},
        )
    )
    repos.facts.assert_fact(campaign.id, npc.id, "is hiding", "a Redbrand deserter")

    server = SessionServer(repos, campaign.id, "Agent session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, campaign, npc
    server.stop()


async def _login(server, username, password, timeout=10.0):
    """Log in and keep pumping.

    The pump is deliberately left running: a test that stops reading the socket
    the moment login finishes would see nothing sent afterwards, and would pass
    for the wrong reason when checking that something did *not* arrive.
    """
    heard: list[tuple[Member, str]] = []

    async def on_said(_session, member, text):
        heard.append((member, text))

    session = AgentSession(
        f"ws://127.0.0.1:{server.port}", username, password, on_said
    )

    async def pump():
        try:
            await session.run()
        except asyncio.CancelledError:
            raise
        except Exception:  # the socket closing is how this ends
            pass

    asyncio.create_task(pump())
    async with asyncio.timeout(timeout):
        while session.table.me is None:
            await asyncio.sleep(0.02)
    return session, heard


async def _settle(seconds: float = 0.4) -> None:
    """Let everything in flight arrive."""
    await asyncio.sleep(seconds)


# ------------------------------------------------------------------- logging in


def test_a_second_implementation_can_log_in(hosted, qapp):
    """The protocol is real if something that is not the app can speak it."""
    server, campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        await _settle()
        return session

    session = _spin(qapp, go())
    assert session.table.me is not None
    assert session.table.me.role == "agent"
    assert session.table.campaign == "Phandalin"


def test_a_wrong_password_is_refused(hosted, qapp):
    server, _campaign, _npc = hosted

    async def go():
        session = AgentSession(
            f"ws://127.0.0.1:{server.port}",
            "autopilot",
            "not-the-password",
            lambda *_: None,
        )
        with pytest.raises(LoginFailed):
            await session.run()

    _spin(qapp, go())


# ---------------------------------------------------------------- what it knows


def test_it_receives_the_canon(hosted, qapp):
    """It answers from what is true, so the fact log has to arrive."""
    server, _campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        await _settle()
        return session

    session = _spin(qapp, go())
    assert any(f["object"] == "a Redbrand deserter" for f in session.table.facts)


def test_it_receives_the_dms_secrets(hosted, qapp):
    """Standing in for the DM means seeing what they see. A player would not."""
    server, _campaign, npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        await _settle()
        return session

    session = _spin(qapp, go())
    entity = session.table.entities[npc.id]
    assert "Redbrand" in entity["data"]["secrets"]


def test_a_player_connecting_the_same_way_gets_neither(hosted, qapp):
    """The same client, a different login: the host decides, not the client."""
    server, _campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "marco", "goblin-teeth")
        await _settle()
        return session

    session = _spin(qapp, go())
    assert session.table.facts == [], "a player must never receive the canon"
    for entity in session.table.entities.values():
        assert "secrets" not in (entity.get("data") or {})


# ------------------------------------------------------------- what it may say


def test_it_will_not_speak_with_autopilot_off(hosted, qapp):
    server, _campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        await _settle()
        return await session.say("The innkeeper looks up.")

    assert _spin(qapp, go()) is False


def test_it_speaks_once_autopilot_is_on(hosted, qapp):
    server, _campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        server.set_autopilot(True, by="Genna")
        await _settle()
        return await session.say("The innkeeper looks up.")

    assert _spin(qapp, go()) is True


def test_it_learns_autopilot_changed_without_reconnecting(hosted, qapp):
    server, _campaign, _npc = hosted

    async def go():
        session, _ = await _login(server, "autopilot", "let-me-run-it")
        await _settle()
        assert session.table.autopilot is False
        server.set_autopilot(True, by="Genna")
        await _settle()
        return session.table.autopilot

    assert _spin(qapp, go()) is True


# ------------------------------------------------------------------ the prompt


def test_the_prompt_leads_with_the_canon():
    table = Table(campaign="Phandalin")
    table.entities = {1: {"id": 1, "name": "Toblen", "kind": "npc"}}
    table.facts = [{"subject": 1, "predicate": "is hiding", "object": "a deserter"}]

    prompt = build_prompt(table, "Elara", "Who are you?")

    assert prompt.index("CANON") < prompt.index("WHO AND WHERE")
    assert "Toblen is hiding a deserter" in prompt


def test_the_prompt_carries_the_line_being_answered():
    prompt = build_prompt(Table(), "Elara", "I check the door for traps.")
    assert "Elara says: I check the door for traps." in prompt


def test_recent_chat_is_bounded():
    """An agent needs the scene, not the campaign. Unbounded is a slow leak."""
    table = Table()
    for i in range(200):
        table.remember("Elara", "player", f"line {i}")

    assert len(table.recent) == Table.RECENT_LIMIT
    assert table.recent[-1]["text"] == "line 199"


def test_an_empty_table_still_builds_a_prompt():
    """First message of a brand new campaign must not crash it."""
    assert "Answer as the DM." in build_prompt(Table(), "Elara", "Hello?")


# ------------------------------------------------------------------------ util


def _spin(qapp, coro, timeout=15.0):
    """Run a coroutine while keeping the Qt host's event loop turning.

    The host is a Qt server and the agent is asyncio; in real use they are in
    different processes. Here they share one, so both loops have to advance.
    """
    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(coro)
        deadline = loop.time() + timeout
        while not task.done():
            qapp.processEvents()
            loop.run_until_complete(asyncio.sleep(0.001))
            if loop.time() > deadline:
                task.cancel()
                raise TimeoutError("the agent did not finish in time")
        return task.result()
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for leftover in pending:
            leftover.cancel()
        if pending:
            # Built inside the loop: asyncio.gather() called from out here
            # binds to whatever loop is current, which is not this one.
            async def drain():
                await asyncio.gather(*pending, return_exceptions=True)

            loop.run_until_complete(drain())
        loop.close()
