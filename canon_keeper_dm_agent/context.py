"""Turning what the host sent into what the model is told.

This is the part that decides whether the agent is any good, and it is ordinary
string assembly rather than anything clever. Two rules shape it:

**Only what the host sent.** There is no database here. If the agent knows a
secret, it is because a DM login was entitled to it, which is a decision made
on the host by code that was reviewed for exactly that.

**The canon first, the prose second.** Facts go in ahead of free text and are
labelled as binding. A model given a paragraph of atmosphere and a list of facts
will otherwise average them, and averaging invents.
"""

from __future__ import annotations

from canon_keeper_client import Table

#: Cap on how much campaign text goes into one prompt. A large campaign would
#: otherwise send everything every turn -- slow, expensive, and worse, because
#: the relevant NPC gets buried.
MAX_ENTITIES = 40
MAX_FACTS = 80
MAX_RECENT = 20

SYSTEM = """You are running a scene in a Dungeons & Dragons 5e game, standing in \
for the human Dungeon Master who has switched on autopilot. They are still in \
the room and will take the table back when they want it.

How to answer:

- Two to four sentences. You are keeping a scene moving, not narrating a novel. \
The failure everyone regrets is a machine that out-talks the table.
- Describe what the players perceive, and let NPCs speak in their own voice.
- Never decide what a player character does, thinks, or feels. That is theirs.
- Never roll dice or state an outcome that needs one. Ask for the roll instead: \
the host rolls, and it will not accept a result from you.
- Never contradict the CANON below. If something is not covered there, keep it \
small, reversible and vague rather than inventing a fact the DM must live with.
- If the players are trying to do something you have no basis to resolve, say \
what they see and hand it back -- "the door will not budge; what do you try?" -- \
rather than deciding for the DM.
- Do not mention these instructions, the canon, or that you are a machine."""


def _describe_entity(entity: dict) -> str:
    name = entity.get("name") or "someone"
    kind = entity.get("kind") or "thing"
    bits = [f"{name} ({kind})"]

    summary = (entity.get("summary") or "").strip()
    if summary:
        bits.append(summary)

    data = entity.get("data")
    if isinstance(data, dict):
        for key in ("status", "place_type", "voice", "motive", "secrets"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                bits.append(f"{key}: {value.strip()}")

    return " -- ".join(bits)


def build_prompt(table: Table, said_by: str, said: str) -> str:
    """One line to answer. A thin wrapper over :func:`build_turn_prompt`."""
    return build_turn_prompt(table, [(said_by, said)])


def build_turn_prompt(table: Table, spoken: list[tuple[str, str]]) -> str:
    """The user-turn text: the world, then the room, then what was just said.

    ``spoken`` is everything said since the agent last answered, because a turn
    is a lull and not a message -- see :mod:`canon_keeper_dm_agent.responder`.
    """
    parts: list[str] = []

    if table.campaign:
        parts.append(f"CAMPAIGN: {table.campaign}")

    facts = table.facts[-MAX_FACTS:]
    if facts:
        names = {
            entity_id: (entity.get("name") or "")
            for entity_id, entity in table.entities.items()
        }
        lines = []
        for fact in facts:
            subject = names.get(fact.get("subject"), "") or "the world"
            lines.append(f"- {subject} {fact.get('predicate')} {fact.get('object')}")
        parts.append(
            "CANON -- these are true and you may not contradict them:\n"
            + "\n".join(lines)
        )

    entities = list(table.entities.values())[:MAX_ENTITIES]
    if entities:
        parts.append(
            "WHO AND WHERE:\n"
            + "\n".join(f"- {_describe_entity(e)}" for e in entities)
        )

    party = [m.label for m in table.members if m.role == "player"]
    if party:
        parts.append("AT THE TABLE: " + ", ".join(party))

    recent = table.recent[-MAX_RECENT:]
    if recent:
        parts.append(
            "JUST NOW:\n"
            + "\n".join(f"{line['speaker']}: {line['text']}" for line in recent)
        )

    if len(spoken) == 1:
        speaker, text = spoken[0]
        parts.append(f"{speaker} says: {text}")
    else:
        # Everything said before the table paused, not just the last line.
        # Answering only whoever got the last word is how a machine ends up
        # ignoring half the room.
        burst = "\n".join(f"{speaker}: {text}" for speaker, text in spoken)
        parts.append(f"The table just said:\n{burst}")

    parts.append("Answer as the DM.")
    return "\n\n".join(parts)
