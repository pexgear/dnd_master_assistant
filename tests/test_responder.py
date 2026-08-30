"""When the agent takes its turn.

The first version answered every line a player typed, which at a table is three
people talking to each other and being interrupted three times -- each answer
arriving seconds late, addressed to something the conversation has left behind.

A turn is a lull, not a message. These tests are about that, and about the two
things that fall out of it: only one answer in flight, and the human winning any
race with the machine.
"""

from __future__ import annotations

import asyncio

import pytest

from canon_keeper_client import Table
from canon_keeper_dm_agent.responder import Responder

#: Short enough to keep the suite quick, long enough to be a real pause.
PAUSE = 0.05


class _Member:
    def __init__(self, role: str, label: str = "Elara") -> None:
        self.role = role
        self.label = label


class _Session:
    def __init__(self, autopilot: bool = True) -> None:
        self.table = Table(autopilot=autopilot)


@pytest.fixture
def spoken() -> list[str]:
    return []


@pytest.fixture
def turns() -> list[list[tuple[str, str]]]:
    """Every batch of lines the model was asked about."""
    return []


@pytest.fixture
def responder(spoken, turns) -> Responder:
    def answer(_table, lines):
        turns.append(list(lines))
        return f"answering {len(lines)} line(s)"

    async def say(text: str) -> None:
        spoken.append(text)

    return Responder(answer, say, quiet_for=PAUSE)


async def _settle(multiple: float = 6.0) -> None:
    """Long enough for a pause to elapse and the answer to be delivered."""
    await asyncio.sleep(PAUSE * multiple)


# ------------------------------------------------------------------- one turn


@pytest.mark.asyncio
async def test_it_answers_after_the_table_pauses(responder, spoken):
    session = _Session()
    await responder.heard(session, _Member("player"), "I open the door.")

    assert spoken == [], "it must not answer instantly"
    await _settle()
    assert len(spoken) == 1


@pytest.mark.asyncio
async def test_a_burst_becomes_one_answer(responder, spoken, turns):
    """Three players talking is one turn, not three interruptions."""
    session = _Session()
    for text in ("I open the door.", "Wait!", "Too late."):
        await responder.heard(session, _Member("player"), text)
        await asyncio.sleep(PAUSE / 4)

    await _settle()

    assert len(spoken) == 1
    assert len(turns[0]) == 3, "all three lines should be answered together"


@pytest.mark.asyncio
async def test_every_speaker_in_the_burst_reaches_the_model(responder, turns):
    session = _Session()
    await responder.heard(session, _Member("player", "Elara"), "I go left.")
    await responder.heard(session, _Member("player", "Mirt"), "I go right.")

    await _settle()

    assert {speaker for speaker, _text in turns[0]} == {"Elara", "Mirt"}


@pytest.mark.asyncio
async def test_more_talking_resets_the_pause(responder, spoken):
    """It waits for the table to stop, not for a fixed time after the first line."""
    session = _Session()
    for _ in range(4):
        await responder.heard(session, _Member("player"), "still talking")
        await asyncio.sleep(PAUSE * 0.6)

    assert spoken == [], "it should still be waiting"
    await _settle()
    assert len(spoken) == 1


# -------------------------------------------------------- one answer in flight


@pytest.mark.asyncio
async def test_it_does_not_start_a_second_answer_while_thinking(spoken, turns):
    """Otherwise a slow model produces two replies racing each other."""
    started = asyncio.Event()

    def slow_answer(_table, lines):
        turns.append(list(lines))
        started.set()
        # Blocking on purpose: this runs in a worker thread, as the real one does.
        import time

        time.sleep(PAUSE * 4)
        return "a considered reply"

    async def say(text):
        spoken.append(text)

    responder = Responder(slow_answer, say, quiet_for=PAUSE)
    session = _Session()

    await responder.heard(session, _Member("player"), "first")
    await asyncio.wait_for(started.wait(), timeout=2)

    # The table keeps talking while it thinks.
    await responder.heard(session, _Member("player"), "second")
    await responder.heard(session, _Member("player"), "third")
    await _settle(20)

    assert len(spoken) == 2, "one answer for the first line, one for the rest"
    assert len(turns) == 2
    assert len(turns[1]) == 2, "what arrived while thinking becomes the next turn"


# ------------------------------------------------------------ the human wins


@pytest.mark.asyncio
async def test_the_dm_speaking_cancels_a_queued_answer(responder, spoken):
    """They answered. Nobody needs it answered twice."""
    session = _Session()
    await responder.heard(session, _Member("player"), "Is the innkeeper in?")
    await responder.heard(session, _Member("dm", "Genna"), "He is, and he looks up.")

    await _settle()

    assert spoken == []


@pytest.mark.asyncio
async def test_the_dm_is_never_answered(responder, spoken):
    session = _Session()
    await responder.heard(session, _Member("dm", "Genna"), "Roll initiative.")
    await _settle()
    assert spoken == []


@pytest.mark.asyncio
async def test_another_agent_is_never_answered(responder, spoken):
    """Two agents answering each other is a loop with a bill attached."""
    session = _Session()
    await responder.heard(session, _Member("agent", "Autopilot"), "The door creaks.")
    await _settle()
    assert spoken == []


# -------------------------------------------------------------- the switch


@pytest.mark.asyncio
async def test_nothing_happens_while_autopilot_is_off(responder, spoken, turns):
    session = _Session(autopilot=False)
    await responder.heard(session, _Member("player"), "I open the door.")
    await _settle()

    assert spoken == []
    assert turns == [], "no model call either -- that is real money"


@pytest.mark.asyncio
async def test_switching_off_during_the_pause_stops_the_answer(responder, spoken, turns):
    """The button promises to take effect immediately, pause or no pause."""
    session = _Session()
    await responder.heard(session, _Member("player"), "I open the door.")
    session.table.autopilot = False

    await _settle()

    assert spoken == []
    assert turns == [], "it should not even ask the model"


# ------------------------------------------------------------------ robustness


@pytest.mark.asyncio
async def test_a_failing_model_does_not_end_the_session(spoken):
    calls = []

    def explode(_table, lines):
        calls.append(lines)
        raise RuntimeError("the API is down")

    async def say(text):
        spoken.append(text)

    responder = Responder(explode, say, quiet_for=PAUSE)
    session = _Session()

    await responder.heard(session, _Member("player"), "hello")
    await _settle()

    assert calls, "it tried"
    assert spoken == [], "and said nothing rather than crashing"

    # And it still works next time.
    await responder.heard(session, _Member("player"), "still there?")
    await _settle()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_an_empty_reply_is_not_said(spoken):
    async def say(text):
        spoken.append(text)

    responder = Responder(lambda _t, _l: "  ", say, quiet_for=PAUSE)
    session = _Session()

    await responder.heard(session, _Member("player"), "hello")
    await _settle()

    assert spoken == [], "whitespace is not an answer"


@pytest.mark.asyncio
async def test_closing_drops_anything_queued(responder, spoken):
    session = _Session()
    await responder.heard(session, _Member("player"), "hello")

    await responder.aclose()
    await _settle()

    assert spoken == []


@pytest.mark.asyncio
async def test_heard_returns_immediately(responder):
    """It must never hold up the socket read: that is what caused the pile-up."""
    session = _Session()
    loop = asyncio.get_running_loop()

    before = loop.time()
    await responder.heard(session, _Member("player"), "hello")
    elapsed = loop.time() - before

    assert elapsed < PAUSE, "heard() must not wait for the answer"
