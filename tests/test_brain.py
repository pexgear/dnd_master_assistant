"""What the agent sends, and what it does when the API objects.

No model is called here. The request is built from a hard-coded idea of what
each model accepts, and that idea is a list of other people's models kept in a
program about elves -- it will go out of date. So the interesting behaviour is
what happens when it is wrong.
"""

from __future__ import annotations

import pytest

from canon_keeper_client import Table
from canon_keeper_dm_agent.brain import (
    EFFORT,
    MAX_TOKENS,
    MAX_TOKENS_WITH_TOOLS,
    MAX_TOOL_ROUNDS,
    PRICES,
    SUPPORTS_EFFORT,
    Brain,
    _objects_to_effort,
)
from canon_keeper_dm_agent.tools import TOOL_NAMES


class _Response:
    """Enough of a Messages response to be recorded."""

    class _Usage:
        input_tokens = 1200
        output_tokens = 80
        cache_read_input_tokens = 0

    class _Text:
        type = "text"
        text = "The innkeeper looks up as you come in."

    content = [_Text()]
    usage = _Usage()


class _Client:
    """Records what was sent, and can be told to object once."""

    def __init__(self, objects_to: str = "") -> None:
        self.calls: list[dict] = []
        self._objects_to = objects_to
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._objects_to and len(self.calls) == 1:
            raise RuntimeError(self._objects_to)
        return _Response()


def _brain(model: str, client: _Client) -> Brain:
    brain = Brain(model=model)
    brain._client = client
    return brain


LINES = [("Elara", "I walk up to the bar.")]


# ------------------------------------------------------------------ the request


def test_a_model_that_takes_effort_gets_it():
    client = _Client()
    _brain("claude-opus-5", client).answer(Table(), LINES)

    assert client.calls[0]["output_config"] == {"effort": EFFORT}


def test_a_model_that_does_not_is_not_sent_it():
    """Haiku 4.5 refuses the whole request over this."""
    client = _Client()
    _brain("claude-haiku-4-5", client).answer(Table(), LINES)

    assert "output_config" not in client.calls[0]


def test_the_reply_is_returned():
    client = _Client()
    reply = _brain("claude-opus-5", client).answer(Table(), LINES)

    assert reply == "The innkeeper looks up as you come in."


def test_the_answer_is_capped():
    """The agent's characteristic failure is talking too much."""
    client = _Client()
    _brain("claude-opus-5", client).answer(Table(), LINES)

    assert client.calls[0]["max_tokens"] == MAX_TOKENS
    assert MAX_TOKENS <= 500, "four sentences, not four paragraphs"


# ---------------------------------------------------------------- the tools
#
# Autopilot that can only talk describes the ambush and leaves the DM to build
# it. These are about the loop that lets one turn do both -- and about it
# stopping, because a model arguing with itself while a table waits is worse
# than a turn that quietly ends.


class _ToolUse:
    type = "tool_use"
    id = "call_1"
    name = "start_combat"
    input = {"name": "The cave", "width": 10, "height": 8}


class _ToolResponse:
    stop_reason = "tool_use"
    content = [_ToolUse()]
    usage = _Response._Usage()


class _Scripted:
    """Answers with whatever the test lined up, in order."""

    def __init__(self, *responses) -> None:
        self.calls: list[dict] = []
        self._responses = list(responses)
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._responses:
            return self._responses.pop(0)
        return _Response()


def test_no_tools_are_offered_when_it_cannot_act():
    """A model told about a button it has not been given will try to press it."""
    client = _Client()
    _brain("claude-opus-5", client).answer(Table(), LINES)

    assert "tools" not in client.calls[0]
    assert "Running a fight" not in client.calls[0]["system"]


def test_the_tools_are_offered_when_it_can():
    client = _Scripted(_Response())
    ran = []
    _brain("claude-opus-5", client).answer(
        Table(), LINES, run_tool=lambda name, args: ran.append(name) or "done"
    )

    sent = {tool["name"] for tool in client.calls[0]["tools"]}
    assert sent == TOOL_NAMES
    assert "Running a fight" in client.calls[0]["system"]


def test_a_tool_call_is_run_and_answered():
    client = _Scripted(_ToolResponse(), _Response())
    ran: list[tuple] = []

    reply = _brain("claude-opus-5", client).answer(
        Table(),
        LINES,
        run_tool=lambda name, args: ran.append((name, args)) or "The fight is on.",
    )

    assert ran == [("start_combat", {"name": "The cave", "width": 10, "height": 8})]
    assert len(client.calls) == 2, "it asks again with the result"
    results = client.calls[1]["messages"][-1]["content"]
    assert results[0]["type"] == "tool_result"
    assert results[0]["tool_use_id"] == "call_1"
    assert results[0]["content"] == "The fight is on."
    assert reply == "The innkeeper looks up as you come in."


def test_it_gives_up_rather_than_looping_forever():
    """A model that only ever asks for tools must not hold the table open."""
    client = _Scripted(*[_ToolResponse() for _ in range(MAX_TOOL_ROUNDS + 4)])

    reply = _brain("claude-opus-5", client).answer(
        Table(), LINES, run_tool=lambda name, args: "done"
    )

    assert len(client.calls) == MAX_TOOL_ROUNDS
    assert reply == "", "no prose, because it never wrote any"


def test_acting_gets_more_room_than_talking():
    """The cap on speech is the system prompt; this one only stops truncation."""
    client = _Scripted(_Response())
    _brain("claude-opus-5", client).answer(
        Table(), LINES, run_tool=lambda name, args: "done"
    )

    assert client.calls[0]["max_tokens"] == MAX_TOKENS_WITH_TOOLS
    assert MAX_TOKENS_WITH_TOOLS > MAX_TOKENS


def test_every_round_of_a_turn_is_paid_for():
    """Two model calls cost two model calls, whatever the table saw."""
    client = _Scripted(_ToolResponse(), _Response())
    brain = _brain("claude-opus-5", client)

    brain.answer(Table(), LINES, run_tool=lambda name, args: "done")

    assert brain.turns == 2
    assert brain.total.input_tokens == 2400


# ------------------------------------------------- when the list is out of date


def test_it_retries_without_effort_when_the_model_objects():
    """Believe the API over the hard-coded list."""
    client = _Client(objects_to="This model does not support the effort parameter.")
    reply = _brain("claude-opus-5", client).answer(Table(), LINES)

    assert len(client.calls) == 2
    assert "output_config" in client.calls[0]
    assert "output_config" not in client.calls[1]
    assert reply, "the turn should still produce an answer"


def test_and_remembers_not_to_send_it_again():
    """Otherwise every turn pays for the same rejected round trip."""
    client = _Client(objects_to="This model does not support the effort parameter.")
    brain = _brain("claude-opus-5", client)

    brain.answer(Table(), LINES)
    brain.answer(Table(), LINES)

    assert len(client.calls) == 3, "two calls for the first turn, one for the second"
    assert "output_config" not in client.calls[2]


def test_any_other_failure_is_raised():
    """A retry loop that swallows a bad key would be worse than the bad key."""
    client = _Client(objects_to="Error code: 401 - API key is invalid.")

    with pytest.raises(RuntimeError, match="401"):
        _brain("claude-opus-5", client).answer(Table(), LINES)

    assert len(client.calls) == 1, "no pointless retry"


@pytest.mark.parametrize(
    "message,expected",
    [
        ("This model does not support the effort parameter.", True),
        ("Unsupported parameter: effort", True),
        ("API key is invalid.", False),
        ("anthropic-workspace-id is required", False),
        ("rate limited, please retry", False),
    ],
)
def test_only_an_effort_objection_counts(message, expected):
    assert _objects_to_effort(RuntimeError(message)) is expected


# ------------------------------------------------------------------ the counting


def test_a_turn_is_counted_and_priced():
    client = _Client()
    brain = _brain("claude-opus-5", client)

    brain.answer(Table(), LINES)

    assert brain.turns == 1
    assert brain.total.input_tokens == 1200
    assert brain.total.output_tokens == 80
    assert brain.total.dollars > 0


def test_a_retried_turn_is_counted_once():
    """The rejected call produced nothing, and billed nothing."""
    client = _Client(objects_to="This model does not support the effort parameter.")
    brain = _brain("claude-opus-5", client)

    brain.answer(Table(), LINES)

    assert brain.turns == 1


def test_turns_add_up():
    client = _Client()
    brain = _brain("claude-opus-5", client)

    brain.answer(Table(), LINES)
    brain.answer(Table(), LINES)

    assert brain.turns == 2
    assert brain.total.output_tokens == 160


# ------------------------------------------------------------------ the tables


def test_every_priced_model_is_a_plausible_id():
    """Date suffixes are a recurring mistake; the ids are complete as they are."""
    for model in PRICES:
        assert model.startswith("claude-")
        assert not model.rstrip("0123456789").endswith("-2"), (
            f"{model} looks like it has a date appended"
        )


def test_the_effort_list_and_the_price_list_agree_where_they_overlap():
    from canon_keeper.panels.table.agent_settings import MODELS

    offered = {model for model, _label in MODELS}
    assert offered <= set(PRICES), "an offered model with no price shows no cost"
    # Not every offered model takes effort -- that is the whole point -- but the
    # ones claimed to must at least be offered or known.
    assert "claude-haiku-4-5" not in SUPPORTS_EFFORT
