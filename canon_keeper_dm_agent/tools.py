"""What the agent can *do*, as opposed to say.

Until now autopilot could only talk. It would announce that goblins burst from
the rubbish and then leave the DM to build the fight it had just described,
which is the worst of both: the machine took the narrative decision and left the
person the clerical one.

So it gets tools. Five of them, and they are the same five things the Combat
panel's buttons do -- deliberately, because every one of them goes over the wire
to the host and through the same checks the DM's own app goes through. An agent
cannot produce a fight the app could not have produced itself, and the host
refuses all of it the moment autopilot goes off.

Two rules shape the design.

**Names, not ids.** The model has read a scene, not a database. It says "Brok",
so :meth:`Table.entity_named` resolves that the way a person would and the tool
returns a plain sentence when it cannot.

**Report what the host said, not what we sent.** Every tool waits for the
encounter to come back before answering. A tool that said "moved" the moment it
wrote to a socket would be reporting that it typed.
"""

from __future__ import annotations

import logging

from canon_keeper_protocol import MessageType, grid

_SIMULATE = MessageType.SIMULATE
_GIVE = MessageType.GIVE

log = logging.getLogger("canonkeeper.agent.tools")

#: How the coordinates are explained to the model, once, so the three tools
#: that take squares cannot describe them differently from each other.
WHERE = (
    "Squares are five feet. 0,0 is the middle of the map: x is positive to the "
    "right and negative to the left, y is positive downwards and negative "
    "upwards."
)

#: A fight bigger than this is not a scene, it is a war, and the map stops being
#: readable long before it stops being drawable.
MAX_GRID = 40
#: How many creatures one call may put into a fight. A model that has decided on
#: forty goblins has misunderstood the question.
MAX_COMBATANTS = 20
MAX_OBSTACLES = 60


TOOLS = [
    {
        "name": "start_combat",
        "description": (
            "Start a turn-based fight and lay it out on a square grid, five "
            "feet to the square. Use this when the scene has just become a "
            "fight: after you describe the ambush, in the same turn.\n\n"
            "Place everyone you have described, including the player "
            "characters, at coordinates that match what you just narrated -- if "
            "you said the archer is up on a ledge to the left, give them a "
            "negative x. Add obstacles for the cover you mentioned: rocks, "
            "pillars, an overturned cart. Nobody can stand in an obstacle.\n\n"
            + WHERE
            + "\n\nGive everyone an initiative if you already asked for rolls; "
            "leave it out and the DM will fill it in. This does not roll dice "
            "and does not decide any outcome."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "What to call the fight, e.g. 'The cave mouth'.",
                },
                "width": {
                    "type": "integer",
                    "description": f"Squares across, 5 to {MAX_GRID}.",
                },
                "height": {
                    "type": "integer",
                    "description": f"Squares down, 5 to {MAX_GRID}.",
                },
                "combatants": {
                    "type": "array",
                    "description": "Who is in the fight, and where they stand.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "character": {
                                "type": "string",
                                "description": (
                                    "The creature's name, as it appears in the "
                                    "scene. It must already exist."
                                ),
                            },
                            "x": {"type": "integer", "description": "Column; 0 is the middle."},
                            "y": {"type": "integer", "description": "Row; 0 is the middle."},
                            "initiative": {
                                "type": "integer",
                                "description": "Their initiative, if it is known.",
                            },
                        },
                        "required": ["character"],
                    },
                },
                "obstacles": {
                    "type": "array",
                    "description": (
                        "Squares nobody can stand in and anybody can hide "
                        "behind, as [x, y] pairs."
                    ),
                    "items": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
            },
            "required": ["name", "width", "height"],
        },
    },
    {
        "name": "look_at_the_map",
        "description": (
            "See the fight as it stands: the grid, who is standing where, whose "
            "turn it is, and what is in the way. Use it before moving anyone if "
            "you are not sure where they are.\n\n" + WHERE
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "move_on_map",
        "description": (
            "Move one creature to a square, or take it off the map. Use it when "
            "you have described something moving. Taking a creature off the map "
            "does not remove it from the fight -- that is for something that has "
            "fled or has not arrived yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "Who is moving."},
                "x": {"type": "integer", "description": "Column; 0 is the middle."},
                "y": {"type": "integer", "description": "Row; 0 is the middle."},
                "off_the_map": {
                    "type": "boolean",
                    "description": "True to take them off the map instead.",
                },
            },
            "required": ["character"],
        },
    },
    {
        "name": "set_obstacle",
        "description": (
            "Put something in the way at a square, or take it away. Nobody can "
            "stand there, and it is what a creature hides behind for cover.\n\n"
            + WHERE
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "present": {
                    "type": "boolean",
                    "description": "True to put one there, false to clear it.",
                },
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "attack",
        "description": (
            "Roll an attack. Use it every time something you are running "
            "swings at somebody -- a goblin at a player, a wolf at a horse.\n\n"
            "**Do not describe the outcome yourself.** You do not know it: the "
            "host rolls the d20, compares it to the target's armour class, "
            "rolls the damage and takes the hit points off. What comes back is "
            "what happened, and you narrate that. Saying "
            "\"the goblin's blade bites deep\" before this is inventing a hit.\n\n"
            "Melee reaches one square, diagonals included, so move next to them "
            "first. The weapon has to be on the attacker's sheet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "attacker": {"type": "string", "description": "Who is swinging."},
                "target": {"type": "string", "description": "Who they are hitting."},
                "weapon": {
                    "type": "string",
                    "description": "Which weapon, from the attacker's sheet.",
                },
            },
            "required": ["attacker", "target"],
        },
    },
    {
        "name": "give_item",
        "description": (
            "Put something in a character's inventory. Call it whenever they "
            "pick anything up: loot off a body, a key from a drawer, the coin "
            "the innkeeper hands over.\n\n"
            "Describing the find is not recording it. Without this the sword "
            "exists in the scene and nowhere else, and the player has to type "
            "it in themselves -- which is exactly the clerical job they are not "
            "here to do.\n\n"
            "Write it as a person would on their own sheet: 'a longsword', "
            "'3 torches', 'the bent iron key'. One call per thing found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "Who picks it up, by name.",
                },
                "item": {
                    "type": "string",
                    "description": "What they now have, in their own words.",
                },
            },
            "required": ["character", "item"],
        },
    },
    {
        "name": "simulate_character",
        "description": (
            "Take a player's character over for this fight, or hand it back. "
            "Only when a person has asked you to -- an empty chair, or a player "
            "who says to play theirs. Never on your own initiative."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "character": {"type": "string"},
                "on": {
                    "type": "boolean",
                    "description": "True to play them, false to hand them back.",
                },
            },
            "required": ["character"],
        },
    },
    {
        "name": "propose_turn",
        "description": (
            "Turn what a player just said into the move and attack they meant, "
            "and put it to them to confirm.\n\n"
            "This is how a player takes their turn. They write it in plain "
            "words -- \"I get behind the orc and hit it with my axe\" -- and you "
            "write down what that is in rules: which square they end on, who "
            "they attack, with which weapon. Their app shows them the move as a "
            "dotted line and a ghost, and they accept, refuse, or tell you "
            "something different.\n\n"
            "Use it whenever it is a player character's turn and they have said "
            "what they want to do. Do not narrate the outcome and do not roll: "
            "nothing happens until they accept, and then the host rolls it.\n\n"
            "Weapons must be on that character's sheet. Melee reaches one "
            "square, diagonals included, so move them next to the target "
            "first.\n\n"
            "A turn's movement is limited by speed -- six squares for most "
            "people, thirty feet. Diagonals count as one square. Ask for more "
            "and the host refuses it and says how far they could actually "
            "get.\n\n" + WHERE
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "character": {
                    "type": "string",
                    "description": "Whose turn it is.",
                },
                "move": {
                    "type": "array",
                    "description": (
                        "The square they end on, as [x, y]. Leave it out if "
                        "they are staying where they are."
                    ),
                    "items": {"type": "integer"},
                },
                "target": {
                    "type": "string",
                    "description": "Who they attack, by name. Leave out if nobody.",
                },
                "weapon": {
                    "type": "string",
                    "description": "Which weapon, from their sheet.",
                },
                "text": {
                    "type": "string",
                    "description": (
                        "One line saying what they are about to do, as they "
                        "will read it: 'Move to 0,4 and attack the orc with a "
                        "battleaxe.'"
                    ),
                },
            },
            "required": ["character", "text"],
        },
    },
    {
        "name": "next_turn",
        "description": (
            "Pass the turn to the next creature in the initiative order, and "
            "start the next round when it wraps. Use it once you have resolved "
            "whoever was up."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "end_combat",
        "description": (
            "The fight is over. Stops the clock; everyone stays where they are. "
            "Use it when one side is down, has fled, or has surrendered."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)


class Tools:
    """Runs a tool call against a live session, and says what happened."""

    def __init__(self, session) -> None:
        self._session = session

    @property
    def table(self):
        return self._session.table

    async def run(self, name: str, arguments: dict) -> str:
        """Do it, and return one paragraph the model can read.

        Never raises. A tool that throws would take the whole turn down, and a
        turn taken down mid-combat leaves the table looking at a half-built
        fight; a sentence saying what went wrong lets the model try something
        else.
        """
        if not self.table.autopilot:
            return (
                "Autopilot is off, so the DM is running the table. Nothing was "
                "changed."
            )
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return f"There is no tool called {name!r}."
        try:
            return await handler(arguments or {})
        except Exception as exc:  # noqa: BLE001 - a bad tool call is not fatal
            log.exception("tool %s failed", name)
            return f"That did not work: {exc}"

    # -------------------------------------------------------------- the tools

    async def _start_combat(self, arguments: dict) -> str:
        width = _clamp(arguments.get("width"), 5, MAX_GRID, 20)
        height = _clamp(arguments.get("height"), 5, MAX_GRID, 15)
        await self._session.start_fight(
            str(arguments.get("name") or "The fight"), width, height
        )
        await self._session.wait_for_the_fight()

        missing: list[str] = []
        placed = 0
        for raw in (arguments.get("combatants") or [])[:MAX_COMBATANTS]:
            if not isinstance(raw, dict):
                continue
            entity = self.table.entity_named(str(raw.get("character", "")))
            if entity is None:
                missing.append(str(raw.get("character", "")))
                continue
            x, y = _on_the_grid(raw.get("x"), raw.get("y"), width, height)
            await self._session.enlist(
                entity["id"],
                x=x,
                y=y,
                initiative=_whole(raw.get("initiative")),
            )
            placed += 1

        for square in (arguments.get("obstacles") or [])[:MAX_OBSTACLES]:
            if isinstance(square, (list, tuple)) and len(square) == 2:
                x, y = _on_the_grid(square[0], square[1], width, height)
                if x is not None and y is not None:
                    await self._session.set_terrain(x, y, True)

        await self._session.turn("begin")
        fight = await self._session.wait_for_the_fight()

        report = [f"The fight is on: {placed} in it. " + self._describe(fight)]
        if missing:
            report.append(
                "These are not in the campaign, so they were left out: "
                + ", ".join(name for name in missing if name)
                + ". Only creatures the DM has already made can be put on the map."
            )
        return " ".join(report)

    async def _look_at_the_map(self, _arguments: dict) -> str:
        return self._describe(self.table.encounter)

    async def _move_on_map(self, arguments: dict) -> str:
        combatant, problem = self._find(str(arguments.get("character", "")))
        if problem:
            return problem

        if arguments.get("off_the_map"):
            await self._session.move(combatant["id"], None, None)
            fight = await self._session.wait_for_the_fight()
            return "Taken off the map, still in the order. " + self._describe(fight)

        x, y = _whole(arguments.get("x")), _whole(arguments.get("y"))
        if x is None or y is None:
            return "Say which square, as x and y, or ask to take them off the map."

        before = self._square_of(combatant["id"])
        await self._session.move(combatant["id"], x, y)
        fight = await self._session.wait_for_the_fight()
        if self._square_of(combatant["id"]) == before:
            return (
                f"They did not move to {x},{y} -- it is off the grid, or "
                "somebody or something is already there. "
                + self._describe(fight)
            )
        return f"Moved to {x},{y}. " + self._describe(fight)

    async def _set_obstacle(self, arguments: dict) -> str:
        x, y = _whole(arguments.get("x")), _whole(arguments.get("y"))
        if x is None or y is None:
            return "Say which square, as x and y."
        present = bool(arguments.get("present", True))
        await self._session.set_terrain(x, y, present)
        fight = await self._session.wait_for_the_fight()
        there = [x, y] in (fight.get("obstacles") or [])
        if there != present:
            return (
                f"The square {x},{y} did not change -- it is off the grid, or "
                "somebody is standing in it."
            )
        return ("Something is in the way at " if present else "Cleared ") + f"{x},{y}."

    async def _attack(self, arguments: dict) -> str:
        attacker, problem = self._find(str(arguments.get("attacker", "")))
        if problem:
            return problem
        target, trouble = self._find(str(arguments.get("target", "")))
        if trouble:
            return trouble

        await self._session.swing(
            attacker["id"], target["id"], str(arguments.get("weapon", ""))
        )
        await self._session.wait_for_the_fight()
        # Deliberately not a report of what happened. The roll arrives in the
        # chat like everyone else's, and narrating from a guess here is the
        # exact failure this tool exists to prevent.
        return (
            "Rolled. The result is in the chat -- narrate what it says, not "
            "what you expected."
        )

    async def _give_item(self, arguments: dict) -> str:
        """Into somebody's inventory. Works outside a fight as well as in one."""
        name = str(arguments.get("character", ""))
        item = str(arguments.get("item", "")).strip()
        if not item:
            return "Say what they picked up."

        entity = self.table.entity_named(name)
        if entity is None:
            return f"There is nobody called {name!r} in this campaign."

        await self._session.ask(_GIVE, entity=entity["id"], item=item)
        return f"{entity.get('name')} has it now, on their own sheet."

    async def _simulate_character(self, arguments: dict) -> str:
        combatant, problem = self._find(str(arguments.get("character", "")))
        if problem:
            return problem
        on = bool(arguments.get("on", True))
        await self._session.ask(
            _SIMULATE, combatant=combatant["id"], on=on
        )
        await self._session.wait_for_the_fight()
        return (
            "You are playing them now." if on else "Handed back to their player."
        )

    async def _propose_turn(self, arguments: dict) -> str:
        """Formalise a player's turn and hand it back to them to confirm."""
        actor, problem = self._find(str(arguments.get("character", "")))
        if problem:
            return problem

        target_id = None
        wanted = str(arguments.get("target", "")).strip()
        if wanted:
            target, trouble = self._find(wanted)
            if trouble:
                return trouble
            target_id = target["id"]

        move = arguments.get("move")
        square = None
        if isinstance(move, (list, tuple)) and len(move) == 2:
            fight = self.table.encounter
            x, y = _on_the_grid(
                move[0], move[1],
                int(fight.get("width") or 1), int(fight.get("height") or 1),
            )
            square = None if x is None else [x, y]

        await self._session.propose(
            actor["id"],
            move=square,
            target=target_id,
            weapon=str(arguments.get("weapon", "")),
            text=str(arguments.get("text", "")),
        )
        return (
            "Put to them. They will accept it, refuse it, or say what they "
            "wanted instead -- wait for that rather than narrating the outcome."
        )

    async def _next_turn(self, _arguments: dict) -> str:
        await self._session.turn("next")
        fight = await self._session.wait_for_the_fight()
        return self._describe(fight)

    async def _end_combat(self, _arguments: dict) -> str:
        await self._session.turn("end")
        await self._session.wait_for_the_fight()
        return "The fight is over. Everyone is where they finished."

    # ------------------------------------------------------------- describing

    def _find(self, name: str) -> tuple[dict | None, str]:
        """The combatant a name refers to, or a sentence saying why not."""
        if not self.table.encounter:
            return None, "There is no fight running. Start one first."
        entity = self.table.entity_named(name)
        if entity is None:
            return None, f"There is nobody called {name!r} in this campaign."
        for combatant in self.table.encounter.get("combatants") or []:
            if combatant.get("entity") == entity.get("id"):
                return combatant, ""
        return None, f"{entity.get('name')} is not in this fight."

    def _square_of(self, combatant_id: int):
        for combatant in self.table.encounter.get("combatants") or []:
            if combatant.get("id") == combatant_id:
                return combatant.get("x"), combatant.get("y")
        return None

    def _describe(self, fight: dict) -> str:
        return describe_fight(fight, self.table.entities)


def describe_fight(fight: dict, entities: dict) -> str:
    """The map in a sentence or two, which is what a model can act on.

    The same words go into the prompt at the start of a turn and come back out
    of a tool call in the middle of one. Two descriptions of one map is two
    chances for the model to be told something subtly different about where
    everybody is.
    """
    if not fight:
        return "There is no fight running."

    def name_of(combatant: dict) -> str:
        entity = entities.get(combatant.get("entity"))
        return (entity or {}).get("name") or "someone"

    sides = {
        team.get("id"): team.get("name")
        for team in fight.get("teams") or ()
        if isinstance(team, dict)
    }

    where = []
    for combatant in fight.get("combatants") or []:
        x, y = combatant.get("x"), combatant.get("y")
        spot = grid.label(x, y)
        side = sides.get(combatant.get("team"))
        if side:
            spot += f", on {side}"
        if combatant.get("down"):
            # Said plainly, because a model that thinks a body is still fighting
            # will narrate it swinging at somebody.
            spot += ", down"
        if combatant.get("id") == fight.get("turn"):
            spot += ", up now"
        where.append(f"{name_of(combatant)} at {spot}")

    left, top, right, bottom = grid.bounds(
        int(fight.get("width") or 1), int(fight.get("height") or 1)
    )
    parts = [
        f"{fight.get('name') or 'The fight'}: a "
        f"{fight.get('width')} by {fight.get('height')} grid, x from {left} to "
        f"{right} and y from {top} to {bottom}, round {fight.get('round') or 0}."
    ]
    if where:
        parts.append("Standing: " + "; ".join(where) + ".")
    obstacles = fight.get("obstacles") or []
    if obstacles:
        parts.append(
            "In the way: "
            + ", ".join(grid.label(x, y) for x, y in obstacles[:MAX_OBSTACLES])
            + "."
        )
    return " ".join(parts)


def _whole(value):
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _on_the_grid(x, y, width: int, height: int):
    """A square the map actually has, or ``(None, None)`` for unplaced.

    Clamped rather than refused: a model that put the archer one square off the
    edge meant the edge, and losing the whole placement over it would be worse
    than putting them at the wall.
    """
    column, row = _whole(x), _whole(y)
    if column is None or row is None:
        return None, None
    return grid.clamp(width, height, column, row)


def _clamp(value, low: int, high: int, fallback: int) -> int:
    number = _whole(value)
    return fallback if number is None else max(low, min(high, number))
