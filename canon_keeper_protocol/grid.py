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

from heapq import heappop, heappush


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


#: As far as a route will look before giving up. A route is only ever wanted
#: inside one turn's movement, and the largest map is sixty squares a side, so
#: this is generous by a wide margin -- it exists so a walled-in creature costs
#: a bounded search rather than the whole board.
_MOST_SQUARES_SEARCHED = 4096


def route_between(
    start: tuple[int, int],
    end: tuple[int, int],
    blocked: frozenset[tuple[int, int]] | set[tuple[int, int]] = frozenset(),
    within: tuple[int, int, int, int] | None = None,
) -> list[tuple[int, int]]:
    """The way from one square to another, around whatever is in the way.

    Every square walked through, starting where they were, the same shape
    :func:`steps_between` returns -- and identical to it whenever the straight
    line is clear, which is the ordinary case. Only when something stands in
    that line is a way round looked for.

    Empty when there is no way at all: a creature boxed in by bodies and rock
    has nowhere to walk to, and saying so is better than a line that pretends
    otherwise.

    ``blocked`` is squares nobody may walk through -- creatures standing and
    terrain alike, since neither can be shared. The starting square is walkable
    whatever it says, because somebody is already standing there.

    Worked out here, in the package the app, the agent and every client share,
    for the same reason the coordinate system is: the host sends the route it
    chose, and a client drawing a *different* way round the same rock would be
    showing a walk that never happened.
    """
    start = (int(start[0]), int(start[1]))
    end = (int(end[0]), int(end[1]))
    if start == end:
        return [start]

    straight = steps_between(start, end)
    if not any(square in blocked for square in straight[1:]):
        return straight

    # A*, eight ways, one square a step whichever way it goes -- the same way
    # the app measures distance, so a walk of six squares still costs six. The
    # shortest way matters rather than merely a way: the route's length is what
    # a turn's movement is charged, so a lazy detour would cost real feet.
    came_from: dict[tuple[int, int], tuple[int, int]] = {start: start}
    cost: dict[tuple[int, int], int] = {start: 0}
    frontier: list[tuple[int, int, int, tuple[int, int]]] = [
        (_chebyshev(start, end), 0, 0, start)
    ]
    order = 0
    while frontier and len(came_from) < _MOST_SQUARES_SEARCHED:
        _guess, _so_far, _tie, here = heappop(frontier)
        if here == end:
            return _walk_back(came_from, end)
        for step in _AROUND:
            there = (here[0] + step[0], here[1] + step[1])
            if there in blocked or (within is not None and not _inside(there, within)):
                continue
            walked = cost[here] + 1
            if walked >= cost.get(there, _MOST_SQUARES_SEARCHED):
                continue
            cost[there] = walked
            came_from[there] = here
            order += 1
            heappush(frontier, (walked + _chebyshev(there, end), walked, order, there))
    return []


#: The eight squares touching one, diagonals included.
_AROUND = (
    (-1, -1), (0, -1), (1, -1),
    (-1, 0), (1, 0),
    (-1, 1), (0, 1), (1, 1),
)


def _chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _inside(square: tuple[int, int], edges: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = edges
    return left <= square[0] <= right and top <= square[1] <= bottom


def _walk_back(
    came_from: dict[tuple[int, int], tuple[int, int]], end: tuple[int, int]
) -> list[tuple[int, int]]:
    route = [end]
    while came_from[route[-1]] != route[-1]:
        route.append(came_from[route[-1]])
    route.reverse()
    return route


def label(x, y) -> str:
    """How a square is written and said: ``3,-2``."""
    if x is None or y is None:
        return "off the map"
    return f"{int(x)},{int(y)}"
