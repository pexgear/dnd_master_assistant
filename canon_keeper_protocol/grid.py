"""Where a square is.

One convention, in the one package everything already depends on, because the
app, the agent and the wire all have to agree about it and a coordinate system
defined twice is a coordinate system defined once and copied wrong.

**0,0 is the middle of the map.** x grows to the right and is negative to the
left; y grows downwards and is negative upwards -- the same way a screen works,
and the same way somebody points at a table.

The alternative, counting from a corner, was what this had first. It reads
badly out loud ("you are at fourteen, nine") and it moves everybody's address
the moment the map grows, because the corner it counts from is wherever the
edge happens to be today. A centre does not move. Push the north wall out and
the goblin at 3,-2 is still at 3,-2.

An even-sided map cannot be perfectly centred, so the extra square goes right
and down: sixteen columns run -8 to 7. That is arbitrary but fixed, and it is
why growing a map by one adds a column on alternating sides.
"""

from __future__ import annotations


def origin(width: int, height: int) -> tuple[int, int]:
    """The top-left square of a map that size, as ``(left, top)``."""
    return -(int(width) // 2), -(int(height) // 2)


def bounds(width: int, height: int) -> tuple[int, int, int, int]:
    """``(left, top, right, bottom)``, all inclusive."""
    left, top = origin(width, height)
    return left, top, left + int(width) - 1, top + int(height) - 1


def holds(width: int, height: int, x: int, y: int) -> bool:
    left, top, right, bottom = bounds(width, height)
    return left <= x <= right and top <= y <= bottom


def clamp(width: int, height: int, x: int, y: int) -> tuple[int, int]:
    """The nearest square that exists.

    Used where being one square off the edge should mean the edge rather than
    nothing at all -- an agent that put the archer just outside the room meant
    the wall, and losing the placement over it would be worse.
    """
    left, top, right, bottom = bounds(width, height)
    return max(left, min(right, int(x))), max(top, min(bottom, int(y)))


def steps_between(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Every square walked through, starting where they were.

    One square at a time, diagonals included -- the same way the distance is
    measured, so a walk of six squares is six steps. The host works this out
    once and sends it, rather than every client inventing its own line and
    drawing a slightly different walk.
    """
    x, y = int(start[0]), int(start[1])
    target_x, target_y = int(end[0]), int(end[1])
    walked = [(x, y)]
    # Bounded by the largest map, so a bad pair cannot loop forever.
    for _step in range(120):
        if (x, y) == (target_x, target_y):
            break
        x += (target_x > x) - (target_x < x)
        y += (target_y > y) - (target_y < y)
        walked.append((x, y))
    return walked


def label(x, y) -> str:
    """How a square is written and said: ``3,-2``."""
    if x is None or y is None:
        return "off the map"
    return f"{int(x)},{int(y)}"
