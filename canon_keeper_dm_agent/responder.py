"""Deciding *when* to answer, which matters more than what it answers.

The first version replied to every line a player said. At a real table that is
unbearable: three people talking to each other becomes three interruptions, and
because a model call takes seconds, each one arrives answering something the
conversation has already moved past.

So a turn is not a message. A turn is **a lull**. Lines are gathered as they
arrive, and the agent speaks only once the table has stopped for a moment --
which is roughly what a person waits for.

Three rules follow from that, and each one exists because the alternative is
worse at a table rather than because it is tidier:

- **Only one answer in flight.** Anything said while it is thinking joins the
  next turn instead of starting a competing one.
- **The human wins.** If the DM says something, whatever was queued is dropped.
  They answered; nobody needs it answered twice.
- **Nothing blocks the socket.** :meth:`heard` returns immediately, so the
  client keeps reading while the model is busy.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("canonkeeper.agent.responder")

#: How long the table has to be quiet before the agent takes its turn. People
#: finish a thought across two or three messages, and answering the first half
#: is worse than answering slowly.
QUIET_FOR = 2.5

#: Whose lines are worth answering. Not the DM's -- they are in the room, and
#: not another agent's, which is a loop with a bill attached.
ANSWERS_TO = ("player",)

#: Roles whose speaking cancels a pending answer.
TAKES_OVER = ("dm",)


class Responder:
    """Gathers what was said and answers once the table pauses."""

    def __init__(
        self,
        answer,
        say,
        *,
        quiet_for: float = QUIET_FOR,
        answers_to: tuple[str, ...] = ANSWERS_TO,
        on_busy=None,
    ) -> None:
        #: ``answer(table, lines) -> str``. Blocking; run off the loop.
        self._answer = answer
        #: ``say(text) -> awaitable[bool]``.
        self._say = say
        self._quiet_for = quiet_for
        self._answers_to = answers_to
        #: ``on_busy(bool)`` -- called around the model call, so the table can
        #: see that the silence is someone thinking rather than nothing at all.
        self._on_busy = on_busy

        self._pending: list[tuple[str, str]] = []
        self._timer: asyncio.Task | None = None
        self._answering = False

    # -------------------------------------------------------------- listening

    async def heard(self, session, member, text: str) -> None:
        """Note a line. Returns at once -- the answer happens on its own task."""
        if member.role in TAKES_OVER:
            # The DM spoke. Whatever was queued was for them to answer, and
            # they have.
            if self._pending or self._timer is not None:
                log.info("the DM answered; dropping %d queued line(s)", len(self._pending))
            self._discard()
            return

        if member.role not in self._answers_to:
            return
        if not session.table.autopilot:
            log.debug("staying quiet: autopilot is off")
            return

        self._pending.append((member.label, text))
        self._restart_timer(session)

    def _restart_timer(self, session) -> None:
        """Wait for the lull again, because the table is still talking."""
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.create_task(self._after_the_lull(session))

    async def _after_the_lull(self, session) -> None:
        try:
            await asyncio.sleep(self._quiet_for)
        except asyncio.CancelledError:
            return

        if self._answering:
            # Still finishing the last turn. Leave the lines queued: whatever
            # finishes will pick them up rather than starting a second answer.
            return
        await self._take_a_turn(session)

    # ---------------------------------------------------------------- speaking

    async def _take_a_turn(self, session) -> None:
        lines, self._pending = self._pending, []
        self._timer = None
        if not lines:
            return

        # Re-checked here rather than only when the line arrived: the DM may
        # have taken the table back during the lull, and the whole promise of
        # that button is that it takes effect immediately.
        if not session.table.autopilot:
            log.info("autopilot went off during the pause; saying nothing")
            return

        self._answering = True
        await self._say_busy(True)
        try:
            reply = await asyncio.to_thread(self._answer, session.table, lines)
        except Exception:  # noqa: BLE001 - one bad turn must not end the session
            log.exception("the model call failed; staying quiet this turn")
            return
        finally:
            self._answering = False
            # In `finally` on purpose: a crash that left the table showing
            # "writing..." forever would be worse than the crash.
            await self._say_busy(False)

        reply = (reply or "").strip()
        if reply:
            await self._say(reply)

        # Said while we were thinking: those are the next turn, and they get
        # their own pause rather than an immediate second answer.
        if self._pending:
            self._restart_timer(session)

    async def _say_busy(self, on: bool) -> None:
        if self._on_busy is None:
            return
        try:
            await self._on_busy(on)
        except Exception:  # noqa: BLE001 - an indicator is not worth a crash
            log.debug("could not report busy", exc_info=True)

    # ----------------------------------------------------------------- closing

    def _discard(self) -> None:
        self._pending.clear()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    async def aclose(self) -> None:
        self._discard()
