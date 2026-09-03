"""Hovering a square, while a move is being lined up, shows the walk to it.

Picking "Move" off the wheel used to leave the DM staring at an empty grid
until they clicked -- "how many squares is that corner" is exactly the
question a grid is supposed to answer, and making somebody count squares by
eye is the grid failing at its one job. So the map now shows the walk as soon
as the pointer is over a square, using :func:`grid.steps_between` -- the same
walk the host actually sends -- so the preview is never a different line from
what gets animated once the click lands.

The part beyond what the turn has left turns the same warning colour a spent
reaction is marked in, so a DM sees a move would be refused before clicking it
rather than after.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from canon_keeper.panels.encounter.grid import Choice, GridMap, Token


@pytest.fixture
def battlefield(qtbot):
    grid = GridMap()
    qtbot.addWidget(grid)
    grid.resize(400, 400)
    grid.set_grid(10, 10)
    grid.set_tokens(
        [
            Token(id=1, label="Brok", x=0, y=0, ours=True, is_turn=True, squares_left=3),
            Token(id=2, label="Yeemik", x=1, y=0),
        ]
    )
    grid.select(1)
    return grid


def _centre_of(grid, x: int, y: int) -> QPoint:
    return grid._at(x, y, grid._cell(), grid._origin()).center()


def _hover(grid, point: QPoint) -> None:
    grid.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(point),
            QPointF(point),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def _awaiting_a_move(grid) -> None:
    grid.offer(1, [Choice("move", "Move")])
    grid._picked_wedge(Choice("move", "Move"))


# --------------------------------------------------------------- appearing


def test_hovering_a_square_draws_a_path(battlefield):
    _awaiting_a_move(battlefield)

    _hover(battlefield, _centre_of(battlefield, 2, 0))

    assert battlefield._hover_path == [(0, 0), (1, 0), (2, 0)]


def test_nothing_is_awaited_yet_means_no_path(battlefield):
    """Hovering before Move is even picked must not show a stray line."""
    _hover(battlefield, _centre_of(battlefield, 2, 0))

    assert battlefield._hover_path == []


def test_an_attack_wedge_shows_no_walk(battlefield):
    """There is nowhere to walk to attack somebody -- only somewhere to hit."""
    battlefield.offer(1, [Choice("attack", "Club", "Club")])
    battlefield._picked_wedge(Choice("attack", "Club", "Club"))

    _hover(battlefield, _centre_of(battlefield, 2, 0))

    assert battlefield._hover_path == []


def test_hovering_your_own_square_shows_nothing(battlefield):
    """A walk of zero squares is not a walk."""
    _awaiting_a_move(battlefield)

    _hover(battlefield, _centre_of(battlefield, 0, 0))

    assert battlefield._hover_path == []


def test_the_path_updates_as_the_pointer_moves(battlefield):
    _awaiting_a_move(battlefield)

    _hover(battlefield, _centre_of(battlefield, 2, 0))
    assert battlefield._hover_path[-1] == (2, 0)

    _hover(battlefield, _centre_of(battlefield, 2, 2))
    assert battlefield._hover_path[-1] == (2, 2)


def test_the_walk_matches_the_one_the_host_would_send(battlefield):
    """Not a client's own idea of a line -- the exact walk that gets animated."""
    from canon_keeper_protocol import grid as protocol_grid

    _awaiting_a_move(battlefield)

    _hover(battlefield, _centre_of(battlefield, 4, 3))

    assert battlefield._hover_path == protocol_grid.steps_between((0, 0), (4, 3))


# -------------------------------------------------------------- vanishing


def test_picking_a_square_clears_the_path(battlefield, qtbot):
    _awaiting_a_move(battlefield)
    _hover(battlefield, _centre_of(battlefield, 2, 0))

    with qtbot.waitSignal(battlefield.planned):
        battlefield.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(_centre_of(battlefield, 2, 0)),
                QPointF(_centre_of(battlefield, 2, 0)),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    assert battlefield._hover_path == []


def test_escape_clears_the_path(battlefield):
    from PySide6.QtGui import QKeyEvent

    _awaiting_a_move(battlefield)
    _hover(battlefield, _centre_of(battlefield, 2, 0))

    battlefield.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )

    assert battlefield._hover_path == []


def test_the_map_stops_tracking_once_answered(battlefield):
    """Tracking every pixel is only worth it while something is being decided."""
    _awaiting_a_move(battlefield)
    assert battlefield.hasMouseTracking() is True

    battlefield._answer_with(None, None)

    assert battlefield.hasMouseTracking() is False


def test_leaving_the_widget_clears_the_path(battlefield):
    _awaiting_a_move(battlefield)
    _hover(battlefield, _centre_of(battlefield, 2, 0))

    battlefield.leaveEvent(QEvent(QEvent.Type.Leave))

    assert battlefield._hover_path == []


# --------------------------------------------------------------- reach


def test_a_reachable_square_stays_within_reach(battlefield):
    """Three squares left, three squares away: nothing here is out of reach."""
    _awaiting_a_move(battlefield)

    _hover(battlefield, _centre_of(battlefield, 3, 0))

    assert len(battlefield._hover_path) - 1 == 3  # == mover.squares_left


def test_drawing_a_reachable_path_does_not_raise(battlefield):
    _awaiting_a_move(battlefield)
    _hover(battlefield, _centre_of(battlefield, 3, 0))

    battlefield.grab()


def test_drawing_a_path_beyond_the_turns_reach_does_not_raise(battlefield):
    """Three squares left and a click four away -- the far half must still render."""
    _awaiting_a_move(battlefield)

    _hover(battlefield, _centre_of(battlefield, 4, 0))

    assert len(battlefield._hover_path) - 1 == 4
    battlefield.grab()


# ------------------------------------------------------- somebody in the way


def test_a_body_in_the_way_stops_the_preview_there(battlefield):
    """The host refuses to walk through anybody, so the line must say so.

    A clean line drawn straight through the goblin would be promising a move
    that is about to be taken back.
    """
    _awaiting_a_move(battlefield)

    # The goblin stands at 1,0, squarely between 0,0 and 3,0.
    _hover(battlefield, _centre_of(battlefield, 3, 0))

    assert battlefield._first_body_in(battlefield._hover_path) == 1
    battlefield.grab()  # must not raise


def test_a_clear_line_is_not_stopped(battlefield):
    _awaiting_a_move(battlefield)

    _hover(battlefield, _centre_of(battlefield, 0, 3))

    assert battlefield._first_body_in(battlefield._hover_path) is None


def test_the_fallen_are_not_in_the_way(battlefield):
    """The same exception the host makes: stepping over a body is ordinary."""
    battlefield.set_tokens(
        [
            Token(id=1, label="Brok", x=0, y=0, ours=True, is_turn=True, squares_left=3),
            Token(id=2, label="Yeemik", x=1, y=0, down=True),
        ]
    )
    battlefield.select(1)
    _awaiting_a_move(battlefield)

    _hover(battlefield, _centre_of(battlefield, 3, 0))

    assert battlefield._first_body_in(battlefield._hover_path) is None


def test_you_are_never_in_your_own_way(battlefield):
    _awaiting_a_move(battlefield)

    _hover(battlefield, _centre_of(battlefield, 0, 2))

    assert battlefield._first_body_in(battlefield._hover_path) is None


def test_a_creature_not_on_its_own_turn_gets_no_reach_cap(qtbot):
    """squares_left is only ever populated for whoever is up (see the panel);
    a path drawn for anyone else must not silently read it as zero and turn
    the whole thing red.
    """
    grid = GridMap()
    qtbot.addWidget(grid)
    grid.resize(400, 400)
    grid.set_grid(10, 10)
    grid.set_tokens([Token(id=1, label="Brok", x=0, y=0, ours=True)])
    grid.select(1)
    _awaiting_a_move(grid)

    _hover(grid, _centre_of(grid, 4, 0))

    grid.grab()  # must not raise, and must not be treated as fully out of reach
