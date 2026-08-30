"""The model call.

Deliberately thin. Everything that makes the agent good or bad lives in
:mod:`canon_keeper_dm_agent.context` -- what it is told -- and in the host, which
decides what it is allowed to know and whether it may speak at all. This module
turns a prompt into a line of text and does nothing else.

The key is the caller's, read from ``ANTHROPIC_API_KEY``. Canon Keeper itself
never needs one; that promise is kept by this living in a separate package.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from canon_keeper_dm_agent.context import SYSTEM, build_turn_prompt
from canon_keeper_client import Table

log = logging.getLogger("canonkeeper.agent.brain")

DEFAULT_MODEL = "claude-opus-5"

#: US dollars per million tokens, (input, output). Used to turn a token count
#: into a number a person can act on -- "seventy pence" is a decision, "412,000
#: tokens" is a fact you then have to go and look up.
#:
#: Prices change. When the model is not listed the tokens are still reported and
#: the cost is left at zero, which is honest, rather than guessed from a
#: neighbouring model, which is not.
PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
#: A hard ceiling on the reply. The agent's characteristic failure is talking
#: too much, and a cap is the one defence that cannot be argued out of.
MAX_TOKENS = 400

#: A table is waiting. Two to four sentences of dialogue is not a hard problem,
#: and a considered answer that arrives after everyone has moved on is worse
#: than a quick one.
EFFORT = "low"


@dataclass
class Usage:
    """What one turn cost."""

    input_tokens: int = 0
    output_tokens: int = 0
    #: Cached input, billed at about a tenth. Reported separately because it is
    #: the difference between a long campaign being affordable and not.
    cached_tokens: int = 0
    dollars: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_tokens + other.cached_tokens,
            self.dollars + other.dollars,
        )


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = PRICES.get(model)
    if rates is None:
        return 0.0
    per_input, per_output = rates
    return (input_tokens * per_input + output_tokens * per_output) / 1_000_000


class BrainUnavailable(RuntimeError):
    """No model to call, and the reason is worth printing."""


def is_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def unavailable_hint() -> str:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "pip install anthropic"
    return "set ANTHROPIC_API_KEY to your key"


class Brain:
    """Produces a DM's line, or raises."""

    def __init__(self, model: str = "", system: str = "", workspace: str = "") -> None:
        self._model = model or DEFAULT_MODEL
        self._system = system or SYSTEM
        #: Some keys -- identity-linked ones -- are refused without the id of
        #: the workspace they belong to. The API says so plainly when it
        #: happens; this is where the answer goes.
        self._workspace = workspace or os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
        self._client = None
        #: Everything this agent has spent since it started.
        self.total = Usage()
        self.turns = 0

    def _ensure_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - import guard
                raise BrainUnavailable("pip install anthropic") from exc
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise BrainUnavailable("set ANTHROPIC_API_KEY to your key")
            headers = (
                {"anthropic-workspace-id": self._workspace} if self._workspace else None
            )
            self._client = anthropic.Anthropic(default_headers=headers)
        return self._client

    def answer(self, table: Table, spoken: list[tuple[str, str]]) -> str:
        """One turn's reply to everything said since the last one."""
        client = self._ensure_client()
        prompt = build_turn_prompt(table, spoken)
        log.debug("prompt is %d characters", len(prompt))

        response = client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=self._system,
            output_config={"effort": EFFORT},
            messages=[{"role": "user", "content": prompt}],
        )
        self._record(response)
        return "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()

    def _record(self, response) -> None:
        """Add one turn to the running total."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        went_in = int(getattr(usage, "input_tokens", 0) or 0)
        came_out = int(getattr(usage, "output_tokens", 0) or 0)
        cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)

        turn = Usage(
            input_tokens=went_in,
            output_tokens=came_out,
            cached_tokens=cached,
            dollars=price(self._model, went_in, came_out),
        )
        self.total = self.total + turn
        self.turns += 1
        log.info(
            "turn %d: %d in, %d out, $%.4f (running $%.2f)",
            self.turns,
            went_in,
            came_out,
            turn.dollars,
            self.total.dollars,
        )
