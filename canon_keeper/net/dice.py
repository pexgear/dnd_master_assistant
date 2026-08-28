"""Dice notation, rolled on the host.

Rolling is server-side on purpose. If each client rolled its own dice and sent
the total, the protocol would be an honour system -- and the one thing a table
needs from shared dice is that nobody can nudge them.

Supports ``d20``, ``2d6+3``, ``4d6kh3`` (keep highest three), ``2d20kl1``
(disadvantage), and a flat ``+2`` modifier on any of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from secrets import SystemRandom

_rng = SystemRandom()

MAX_DICE = 100
MAX_SIDES = 1000

_NOTATION = re.compile(
    r"""^\s*
    (?P<count>\d*)                 # 2      (blank means one die)
    d
    (?P<sides>\d+)                 # d20
    (?:k(?P<keep_dir>[hl])(?P<keep>\d+))?   # kh3 / kl1
    (?:\s*(?P<sign>[+-])\s*(?P<modifier>\d+))?
    \s*$""",
    re.IGNORECASE | re.VERBOSE,
)


class DiceError(ValueError):
    """Bad notation. The message is written to be shown to the user."""


@dataclass(slots=True)
class Roll:
    notation: str
    rolls: list[int]
    kept: list[int] = field(default_factory=list)
    modifier: int = 0
    total: int = 0

    def describe(self) -> str:
        """A one-line rendering: '2d6+3 = [4, 6] +3 = 13'."""
        parts = [f"{self.notation} = {self.rolls}"]
        if len(self.kept) != len(self.rolls):
            parts.append(f"keep {self.kept}")
        if self.modifier:
            parts.append(f"{self.modifier:+d}")
        parts.append(f"= {self.total}")
        return " ".join(parts)


def roll(notation: str) -> Roll:
    match = _NOTATION.match(notation or "")
    if match is None:
        raise DiceError(f"{notation!r} is not dice notation. Try 2d6+3 or 4d6kh3.")

    count = int(match.group("count") or 1)
    sides = int(match.group("sides"))
    if count < 1 or count > MAX_DICE:
        raise DiceError(f"roll between 1 and {MAX_DICE} dice")
    if sides < 2 or sides > MAX_SIDES:
        raise DiceError(f"dice need between 2 and {MAX_SIDES} sides")

    keep = match.group("keep")
    keep_count = int(keep) if keep else count
    if keep_count < 1 or keep_count > count:
        raise DiceError(f"cannot keep {keep_count} of {count} dice")

    modifier = int(match.group("modifier") or 0)
    if match.group("sign") == "-":
        modifier = -modifier

    rolls = [_rng.randint(1, sides) for _ in range(count)]
    if keep and keep_count < count:
        ordered = sorted(rolls, reverse=match.group("keep_dir").lower() == "h")
        kept = ordered[:keep_count]
    else:
        kept = list(rolls)

    return Roll(
        notation=notation.strip(),
        rolls=rolls,
        kept=kept,
        modifier=modifier,
        total=sum(kept) + modifier,
    )
