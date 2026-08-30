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
    PRICES,
    SUPPORTS_EFFORT,
    Brain,
    _objects_to_effort,
)


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
