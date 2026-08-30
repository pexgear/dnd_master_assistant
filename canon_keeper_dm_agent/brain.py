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

from canon_keeper_dm_agent.context import SYSTEM, build_prompt
from canon_keeper_client import Table

log = logging.getLogger("canonkeeper.agent.brain")

DEFAULT_MODEL = "claude-sonnet-5"
#: A hard ceiling on the reply. The agent's characteristic failure is talking
#: too much, and a cap is the one defence that cannot be argued out of.
MAX_TOKENS = 400


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

    def __init__(self, model: str = "", system: str = "") -> None:
        self._model = model or DEFAULT_MODEL
        self._system = system or SYSTEM
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - import guard
                raise BrainUnavailable("pip install anthropic") from exc
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise BrainUnavailable("set ANTHROPIC_API_KEY to your key")
            self._client = anthropic.Anthropic()
        return self._client

    def answer(self, table: Table, said_by: str, said: str) -> str:
        client = self._ensure_client()
        prompt = build_prompt(table, said_by, said)
        log.debug("prompt is %d characters", len(prompt))

        response = client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=self._system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
