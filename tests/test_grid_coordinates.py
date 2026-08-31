"""Where a square is, and why the middle is 0,0.

This convention is shared by the app, the agent and the wire, so it lives in
the protocol package and is tested here on its own. Getting it wrong does not
crash anything -- it puts the archer in the wrong place, quietly, on everybody's
screen at once.
"""

from __future__ import annotations

import pytest

from canon_keeper_protocol import grid


@pytest.mark.parametrize(
    "size,expected",
    [
        (16, (-8, 7)),
        (15, (-7, 7)),
        (10, (-5, 4)),
        (5, (-2, 2)),
        (1, (0, 0)),
    ],
)
def test_a_row_runs_either_side_of_zero(size, expected):
    left, _top, right, _bottom = grid.bounds(size, size)
    assert (left, right) == expected


def test_every_size_has_a_zero():
    """It is what people count from, so it has to exist on every map."""
    for size in range(1, 61):
        left, top, right, bottom = grid.bounds(size, size)
        assert left <= 0 <= right
        assert top <= 0 <= bottom


def test_the_extra_square_of_an_even_map_goes_right_and_down():
    """Arbitrary, but fixed. It is why growing a map alternates sides."""
    left, top, right, bottom = grid.bounds(16, 12)
    assert (left, right) == (-8, 7)
    assert (top, bottom) == (-6, 5)


def test_the_number_of_squares_is_the_width():
    for width, height in ((16, 12), (15, 11), (7, 5)):
        left, top, right, bottom = grid.bounds(width, height)
        assert right - left + 1 == width
        assert bottom - top + 1 == height


def test_what_is_on_the_map_and_what_is_not():
    assert grid.holds(10, 8, 0, 0)
    assert grid.holds(10, 8, -5, -4)
    assert grid.holds(10, 8, 4, 3)
    assert not grid.holds(10, 8, 5, 0)
    assert not grid.holds(10, 8, 0, -5)


def test_clamping_puts_you_at_the_wall():
    assert grid.clamp(10, 8, 99, 99) == (4, 3)
    assert grid.clamp(10, 8, -99, -99) == (-5, -4)
    assert grid.clamp(10, 8, 2, 1) == (2, 1)


def test_a_square_reads_as_people_say_it():
    assert grid.label(3, -2) == "3,-2"
    assert grid.label(0, 0) == "0,0"
    assert grid.label(None, None) == "off the map"


def test_growing_a_map_does_not_move_the_middle():
    """The reason for the whole convention.

    Counting from a corner means every address changes the moment a wall moves.
    Counting from the middle means the goblin at 3,-2 is still at 3,-2 after
    the north wall is pushed out, which is what a DM expects when they press a
    button labelled 'one more row'.
    """
    for width in range(6, 30):
        left, _top, right, _bottom = grid.bounds(width, 10)
        # Every map of every size holds the middle, and holds it at 0,0.
        assert left <= 0 <= right
    # And a square well inside a small map is the same square on a bigger one.
    assert grid.holds(10, 10, 3, -2)
    assert grid.holds(30, 30, 3, -2)


def test_the_repository_uses_the_same_rule(repos):
    """One definition. Two would be one definition copied wrong."""
    campaign = repos.campaigns.ensure_default("Grid")
    encounter = repos.encounters.create(campaign.id, "x", width=16, height=12)
    assert encounter.bounds == grid.bounds(16, 12)
    assert encounter.holds(-8, -6)
    assert not encounter.holds(8, 0)
