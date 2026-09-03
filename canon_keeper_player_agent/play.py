"""Taking one character's turns the way its player would, in words.

The important thing this does *not* do is move anybody. It says what it wants
in plain language -- "I close on the goblin and swing" -- exactly as a person
types it, autopilot turns that into rules and puts it back as a proposal, and
this answers yes. Nothing touches the character until that yes.

Going the long way round is the point:

* **One path, not two.** A stand-in's turn is the same three parties a person's
  turn is -- something proposes, the host checks, the seat accepts. A second
  path that let a machine move a token directly would be the one nobody looks
  at again, and the one where a rule quietly stops being enforced.
* **The DM sees it happen.** A proposal is shown to them as it is for any
  player, so a machine playing a character is as visible as a person doing it.
* **The rules are somebody else's job.** This does not have to know what is
  legal. It says what it wants; the host refuses what it may not have, and the
  refusal is a sentence rather than a silence.

It waits a moment before speaking, so a DM can take the character back or move
the turn on before anything is said, and so the table reads somebody thinking
rather than a machine answering.
"""

from __future__ import annotations

import asyncio
import logging

from canon_keeper_client.session import AgentSession
from canon_keeper_player_agent.tactics import Decision, decide

log = logging.getLogger("canonkeeper.player_agent")

#: How long to wait after the turn arrives before saying anything.
PAUSE = 3.0

#: Squares a turn allows when the host has not said. It always says, for the
#: character whose turn it is, so this is only the first moment of a fight.
DEFAULT_SPEED = 6


class Stand_In:
    """One character, played by this process for as long as it is handed over."""

    def __init__(self, session: AgentSession, pause: float = PAUSE) -> None:
        self._session = session
        self._pause = pause
        #: The turn we have already spoken for. The host publishes the fight
        #: after every change, so without this a turn would be asked for twice.
        self._acted_on: tuple | None = None
        self._busy = False

    @property
    def mine(self) -> dict | None:
        """Our combatant in the running fight, as the host sent it."""
        table = self._session.table
        me = table.me
        if me is None:
            return None
        for combatant in (table.encounter or {}).get("combatants") or []:
            entity = table.entities.get(combatant.get("entity")) or {}
            if entity.get("name") and entity.get("name") == me.character:
                return combatant
        return None

    def _its_my_turn(self) -> bool:
        mine = self.mine
        if not mine or not mine.get("simulated"):
            return False
        return (self._session.table.encounter or {}).get("turn") == mine.get("id")

    # ------------------------------------------------------------ saying it

    async def on_encounter(self, _session: AgentSession) -> None:
        """The fight changed. Speak if it has become our turn."""
        if self._busy or not self._its_my_turn():
            return
        encounter = self._session.table.encounter or {}
        this_turn = (encounter.get("round"), encounter.get("turn"))
        if self._acted_on == this_turn:
            return

        self._busy = True
        try:
            await asyncio.sleep(self._pause)
            # The DM may have moved the turn on, or taken the character back,
            # while we were pausing. Both mean this is no longer ours to play.
            if not self._its_my_turn():
                return
            self._acted_on = this_turn
            await self.take_the_turn()
        finally:
            self._busy = False

    async def take_the_turn(self) -> None:
        """Say what this character does, and do it if nobody else will.

        Two ways round, and which one applies is not this thing's preference:

        **Autopilot is on.** Say it in words and wait. Autopilot turns the
        sentence into rules and puts it back as a proposal, which is answered
        in :meth:`on_action`. That is the path a person's turn takes and it is
        the better one, because the DM sees the proposal.

        **Autopilot is off.** There is nobody to translate, so the turn is
        taken directly on the seat's own authority -- which the host grants for
        this character, on its turn, and nothing else. Still said out loud
        first, so the table reads the same sentence either way.

        Handing a character over and handing the *table* over are two different
        decisions. Somebody who has stepped out should not have their character
        stand still because the DM is running the game themselves.
        """
        mine = self.mine
        if mine is None:
            return
        table = self._session.table
        chosen = self.work_out(mine, table.encounter or {}, table.entities)

        if chosen.does_nothing:
            await self._session.say("I hold where I am.")
            await self._session.turn_done()
            return

        await self._session.say(self.in_words(chosen))
        if self.somebody_will_formalise():
            return
        await self.do_it(mine, chosen)

    def somebody_will_formalise(self) -> bool:
        """Whether anything is here to turn a sentence into a proposal.

        Both halves matter. The switch being on is not enough -- it can be on
        with no agent connected -- and an agent being connected is not enough,
        because the host refuses its chat while the switch is off. Waiting for
        something that is not coming is how a fight stops.
        """
        table = self._session.table
        if not table.autopilot:
            return False
        return any(
            getattr(member, "role", "") == "agent" for member in table.members
        )

    async def do_it(self, mine: dict, chosen: Decision) -> None:
        """Take the turn on the seat's own authority, and end it."""
        if chosen.move is not None:
            await self._session.move(mine["id"], chosen.move[0], chosen.move[1])
        # One attack a turn, and the host is the one counting.
        if chosen.target is not None and not self.action_spent(
            self._session.table.encounter or {}, mine
        ):
            await self._session.swing(mine["id"], chosen.target)
        await self._session.turn_done()

    def in_words(self, chosen: Decision) -> str:
        """The turn as a person would type it, not as a rule would state it.

        Deliberately loose. Autopilot reads a sentence and works out the
        squares, which is the same thing it does for everybody else -- and a
        stand-in that spoke in coordinates would be asking for a different
        service from the one the table uses.
        """
        if not chosen.because:
            return "I hold."
        # Not `capitalize()`: that lowercases everything after the first letter
        # and turns Yeemik into yeemik, which autopilot then has to guess at.
        said = chosen.because.strip()
        return said if said.endswith(".") else said + "."

    # ---------------------------------------------------------- answering it

    async def on_action(self, _session: AgentSession, action: dict) -> None:
        """A turn worked out for us, waiting on a yes.

        Accepted if it is for our character. Anything else is somebody's
        mistake and is refused rather than ignored -- a proposal left hanging
        holds the table up, and the host cannot tell the difference between
        thinking and a stand-in that has stopped listening.
        """
        mine = self.mine
        if mine is None or action.get("combatant") != mine.get("id"):
            return
        log.info("accepting: %s", action.get("text", ""))
        await self._session.answer(str(action.get("id", "")), True)

    # ------------------------------------------------------------- deciding

    def work_out(self, mine: dict, fight: dict, entities: dict) -> Decision:
        """What to do. Overridden by the model, when there is one to ask."""
        return decide(mine, fight, entities, speed=self.squares_left(fight, mine))

    def squares_left(self, fight: dict, mine: dict) -> int:
        """How far this turn may still go, according to the host.

        Read rather than assumed. The host sends the turn's budget -- speed off
        the sheet, how much is spent, whether the action is gone -- to whoever
        the turn belongs to. Guessing would mean asking for turns that get
        refused, which is the one way a machine is worse than an empty chair:
        an empty chair does not argue with the referee.
        """
        budget = fight.get("budget") or {}
        if budget.get("combatant") == mine.get("id"):
            left = budget.get("left")
            if isinstance(left, int):
                return max(0, left)
        return DEFAULT_SPEED

    def action_spent(self, fight: dict, mine: dict) -> bool:
        """Whether the one attack this turn allows has already been taken."""
        budget = fight.get("budget") or {}
        return bool(
            budget.get("combatant") == mine.get("id") and budget.get("acted")
        )
