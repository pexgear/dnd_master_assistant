"""The MCP server has exactly the authority of the login it holds.

That is the whole claim, and it is worth testing against a real host rather than
asserting it in a docstring. A tool call is an ordinary message on the wire, so
a player driving one by voice can do what that player could do by hand -- and
nothing else.

The interesting cases are the refusals: a request for someone else's character,
and an edit that comes back as "sent to your DM" rather than "done".
"""

from __future__ import annotations

import asyncio

import pytest

from canon_keeper.net.server import SessionServer
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper_client import AgentSession
from canon_keeper_mcp.server import CanonKeeperTools, build_server


@pytest.fixture
def hosted(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Phandalin")
    elara = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Elara",
            data={"sheet": {"schema": 1, "hp": 12, "max_hp": 12}},
        )
    )
    villain = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Iarno Albrek",
            data={"secrets": "He is the Redbrand leader."},
        )
    )
    marco = repos.accounts.create(
        campaign.id,
        "marco",
        "goblin-teeth",
        display_name="Marco",
        character_entity_id=elara.id,
    )
    repos.entities.set_owner(elara.id, marco.id)

    server = SessionServer(repos, campaign.id, "MCP session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, campaign, elara, villain
    server.stop()


async def _seat(server, username, password):
    """A logged-in MCP tool surface, still pumping."""
    session = AgentSession(
        f"ws://127.0.0.1:{server.port}", username, password, _ignore
    )

    async def pump():
        try:
            await session.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    asyncio.create_task(pump())
    async with asyncio.timeout(10):
        while session.table.me is None:
            await asyncio.sleep(0.02)
    await asyncio.sleep(0.4)
    return CanonKeeperTools(session)


async def _ignore(*_args) -> None:
    pass


# ------------------------------------------------------------------- the tools


def test_the_server_advertises_its_tools(qapp, hosted):
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return await build_server(tools.session).list_tools()

    names = {tool.name for tool in _spin(qapp, go())}
    assert names == {
        "whats_happening",
        "who_and_where",
        "my_characters",
        "the_fight",
        "say",
        "roll",
        "update_my_character",
    }


def test_the_seat_can_see_a_fight_but_not_touch_it(qapp, hosted, repos):
    """A player does not move tokens in the app, so neither does their seat.

    The read is worth having -- "whose turn is it, and what is next to me" is
    exactly what a player asks. Anything that moves a token is the DM's, and
    there is no tool here that even asks.
    """
    server, campaign, elara, _villain = hosted
    encounter = repos.encounters.create(campaign.id, "The cellar", width=8, height=6)
    repos.encounters.add(encounter.id, elara.id, initiative=15, x=2, y=2)
    repos.encounters.toggle_obstacle(encounter.id, 1, 1)
    repos.encounters.begin(encounter.id)

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return tools.the_fight(), await build_server(tools.session).list_tools()

    fight, offered = _spin(qapp, go())

    assert fight["fighting"] is True
    assert fight["name"] == "The cellar"
    assert fight["whose_turn"] == "Elara"
    assert fight["standing"][0]["x"] == 2
    assert fight["in_the_way"] == [[1, 1]]

    names = {tool.name for tool in offered}
    for forbidden in ("move", "place", "start_combat", "next_turn", "obstacle"):
        assert not any(forbidden in name for name in names), (
            f"a player's seat offers {forbidden}, which is the DM's to do"
        )


def test_no_fight_says_so(qapp, hosted):
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return tools.the_fight()

    assert _spin(qapp, go()) == {"fighting": False}


def test_it_reports_the_scene(qapp, hosted):
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return tools.whats_happening()

    scene = _spin(qapp, go())
    assert scene["campaign"] == "Phandalin"
    assert scene["you"] == "Elara"


def test_it_sees_its_own_character(qapp, hosted):
    server, _campaign, elara, _villain = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return tools.my_characters()

    mine = _spin(qapp, go())
    assert [c["name"] for c in mine] == ["Elara"]


def test_it_never_sees_an_unshared_npcs_secrets(qapp, hosted):
    """Not filtered here. It never arrived."""
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        return tools.who_and_where()

    seen = _spin(qapp, go())
    assert "Iarno Albrek" not in {e["name"] for e in seen}


# ----------------------------------------------------------------- the actions


def test_saying_something_reaches_the_table(qapp, hosted):
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        await tools.say("I check the door for traps.")
        await asyncio.sleep(0.4)
        return server.history()

    said = [m["text"] for m in _spin(qapp, go())]
    assert "I check the door for traps." in said


def test_a_roll_is_asked_for_not_decided(qapp, hosted):
    """The host rolls. A client that invented the number would be ignored."""
    server, *_ = hosted

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        answer = await tools.roll("2d6+3")
        await asyncio.sleep(0.4)
        return answer, server.history()

    answer, history = _spin(qapp, go())
    assert "host" in answer.lower()
    assert any("2d6+3" in m.get("text", "") for m in history)


def test_a_change_is_a_request_not_a_write(qapp, hosted, repos):
    """The honest return value is "sent", because nothing has been applied."""
    server, _campaign, elara, _villain = hosted
    before = repos.entities.get(elara.id).version

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        answer = await tools.update_my_character(
            elara.id, {"data": {"sheet": {"level": 5}}}
        )
        await asyncio.sleep(0.4)
        return answer

    answer = _spin(qapp, go())
    assert "DM" in answer
    assert "approve" in answer.lower() or "refuse" in answer.lower()
    assert repos.entities.get(elara.id).version == before, (
        "an MCP tool call must not write to the campaign"
    )


def test_it_cannot_touch_someone_elses_character(qapp, hosted, repos):
    server, _campaign, _elara, villain = hosted
    before = repos.entities.get(villain.id).version

    async def go():
        tools = await _seat(server, "marco", "goblin-teeth")
        await tools.update_my_character(villain.id, {"summary": "actually harmless"})
        await asyncio.sleep(0.4)

    _spin(qapp, go())
    assert repos.entities.get(villain.id).version == before
    assert repos.entities.get(villain.id).summary != "actually harmless"


# ------------------------------------------------------------------------ util


def _spin(qapp, coro, timeout=20.0):
    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(coro)
        deadline = loop.time() + timeout
        while not task.done():
            qapp.processEvents()
            loop.run_until_complete(asyncio.sleep(0.005))
            if loop.time() > deadline:
                task.cancel()
                raise TimeoutError("the MCP seat did not finish in time")
        return task.result()
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for leftover in pending:
            leftover.cancel()
        if pending:
            async def drain():
                await asyncio.gather(*pending, return_exceptions=True)

            loop.run_until_complete(drain())
        loop.close()
