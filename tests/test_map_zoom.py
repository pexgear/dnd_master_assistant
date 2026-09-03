"""Zooming and panning the battle map.

The map used to size itself to the panel and nothing else, with four little
buttons on its edges that added and removed columns. Those were the wrong
gesture in two ways at once: they changed the *fight* when what you wanted was
a closer look, and they meant a big map in a small dock was a board of specks
with no way to get nearer. The map is now a thing you look at, and how big the
room is belongs to the fight dialog.

Two behaviours are worth pinning. **Fitting survives a resize** -- a map that
has not been zoomed re-fits when the dock changes shape, which is why "fit" is
the absence of a chosen size rather than a number that happens to equal it. And
**zoom is anchored on the pointer**: zooming about the centre slides the thing
you were looking at away from you, and you chase it with two more gestures.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent

from canon_keeper.panels.encounter.grid import (
    MAX_CELL,
    MIN_CELL,
    RULER,
    ZOOM_MAX,
    ZOOM_MIN,
    GridMap,
    Token,
)


@pytest.fixture
def board(qtbot):
    """A big map in a small window, so there is something to pan around."""
    grid = GridMap()
    qtbot.addWidget(grid)
    grid.resize(400, 300)
    grid.set_grid(40, 40)
    grid.set_tokens([Token(id=1, label="Brok", x=0, y=0, ours=True)])
    return grid


def _wheel(grid, at: QPoint, up: bool) -> None:
    grid.wheelEvent(
        QWheelEvent(
            QPointF(at),
            QPointF(at),
            QPoint(0, 0),
            QPoint(0, 120 if up else -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
    )


def _press(grid, key) -> None:
    grid.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    )


# ------------------------------------------------------------------ fitting


def test_a_fresh_map_fits_itself(board):
    assert board.fitting is True


def test_fitting_shows_the_whole_map_when_it_can(qtbot):
    grid = GridMap()
    qtbot.addWidget(grid)
    grid.resize(400, 400)
    grid.set_grid(8, 8)

    assert grid.zoom * 8 <= 400 - RULER


def test_fitting_stops_at_the_legibility_floor(board):
    """Forty squares in four hundred pixels is nine each. Nine is not a token."""
    assert board.zoom == MIN_CELL


def test_fitting_never_makes_giant_squares(qtbot):
    grid = GridMap()
    qtbot.addWidget(grid)
    grid.resize(2000, 2000)
    grid.set_grid(2, 2)

    assert grid.zoom == MAX_CELL


def test_fitting_survives_the_panel_changing_shape(qtbot):
    """The reason fit is the *absence* of a size rather than a number."""
    grid = GridMap()
    qtbot.addWidget(grid)
    grid.set_grid(10, 10)
    grid.resize(400, 400)
    before = grid.zoom

    grid.resize(800, 800)

    assert grid.zoom > before
    assert grid.fitting is True


# ------------------------------------------------------------------ zooming


def test_the_wheel_zooms_in(board):
    before = board.zoom

    _wheel(board, QPoint(200, 150), up=True)

    assert board.zoom > before


def test_the_wheel_zooms_out(board):
    board.zoom_by(4)
    before = board.zoom

    _wheel(board, QPoint(200, 150), up=False)

    assert board.zoom < before


def test_zooming_stops_fitting(board):
    board.zoom_by(1)

    assert board.fitting is False


def test_a_chosen_zoom_survives_the_panel_changing_shape(board):
    board.zoom_by(3)
    chosen = board.zoom

    board.resize(900, 700)

    assert board.zoom == chosen, "a resize overrode what somebody asked for"


def test_zoom_stops_at_the_top(board):
    for _ in range(80):
        board.zoom_by(1)

    assert board.zoom == ZOOM_MAX


def test_zoom_stops_at_the_bottom(board):
    for _ in range(80):
        board.zoom_by(-1)

    assert board.zoom == ZOOM_MIN


def test_one_notch_always_moves(board):
    """Rounding used to eat a notch when the squares were small."""
    board.zoom_by(-40)
    at_the_floor = board.zoom
    board.zoom_by(1)

    assert board.zoom > at_the_floor


def test_zooming_keeps_the_pointer_over_the_same_square(board):
    """Otherwise you close in on a thing and it slides out from under you."""
    at = QPoint(300, 200)
    was = board._square_at(at)
    assert was is not None

    board.zoom_by(1, at)

    assert board._square_at(at) == was


def test_zooming_out_keeps_it_too(board):
    board.zoom_by(5, QPoint(200, 150))
    at = QPoint(280, 180)
    was = board._square_at(at)
    assert was is not None

    board.zoom_by(-1, at)

    assert board._square_at(at) == was


def test_the_whole_map_comes_back(board):
    board.zoom_by(5, QPoint(300, 200))

    board.fit()

    assert board.fitting is True
    assert board.zoom == MIN_CELL


# ------------------------------------------------------------------ keyboard


def test_plus_zooms_in(board):
    before = board.zoom
    _press(board, Qt.Key.Key_Plus)
    assert board.zoom > before


def test_equals_zooms_in_too(board):
    """On most layouts the plus key is the equals key with a shift on it."""
    before = board.zoom
    _press(board, Qt.Key.Key_Equal)
    assert board.zoom > before


def test_minus_zooms_out(board):
    board.zoom_by(4)
    before = board.zoom
    _press(board, Qt.Key.Key_Minus)
    assert board.zoom < before


def test_zero_shows_the_whole_map(board):
    board.zoom_by(4)
    _press(board, Qt.Key.Key_0)
    assert board.fitting is True


# ------------------------------------------------------------------ panning


def test_a_board_that_fits_does_not_pan(qtbot):
    """Nudging a map around inside spare room is a map that never sits still."""
    grid = GridMap()
    qtbot.addWidget(grid)
    grid.resize(900, 900)
    grid.set_grid(6, 6)
    before = grid._origin()

    grid.pan_by(3, 3)

    assert grid._origin() == before


def test_a_board_bigger_than_the_room_pans(board):
    board.zoom_by(6)
    before = board._origin()

    board.pan_by(2, 0)

    assert board._origin() != before


def test_panning_cannot_push_the_board_off_the_screen(board):
    board.zoom_by(6)

    board.pan_by(500, 500)
    origin = board._origin()

    right = origin.x() + board.zoom * 40
    bottom = origin.y() + board.zoom * 40
    assert right >= board.width(), "the board was pushed off to the left"
    assert bottom >= board.height(), "the board was pushed off the top"


def test_the_board_slides_under_the_rulers(board):
    board.zoom_by(6)
    board.pan_by(3, 3)
    origin = board._origin()

    assert origin.x() <= RULER
    assert origin.y() <= RULER


def test_the_rulers_are_still_there_after_panning(board):
    """They used to be pinned to the *board*, so panning took them with it.

    Which is the one thing that must not happen: a coordinate you cannot read
    is a square nobody can say out loud, and saying a square out loud is what
    the rulers exist for.
    """
    board.zoom_by(6)
    board.pan_by(8, 8)

    picture = board.grab().toImage()
    strip = [
        picture.pixelColor(x, y).rgb()
        for y in range(0, RULER)
        for x in range(RULER, min(board.width(), 300))
    ]
    assert len(set(strip)) > 1, "the top ruler is a blank strip"


def test_nothing_from_the_board_paints_into_the_ruler(board):
    """The board runs under the strip once it pans, so it has to be clipped."""
    board.zoom_by(6)
    board.pan_by(8, 8)
    viewport = board._viewport()

    assert viewport.left() == RULER
    assert viewport.top() == RULER


def test_arrow_keys_pan(board):
    board.zoom_by(6)
    before = board._origin()

    _press(board, Qt.Key.Key_Right)

    assert board._origin().x() < before.x()


def test_a_different_map_starts_where_it_should(board):
    """The corner you had scrolled to is not a place on the next fight's map."""
    board.zoom_by(6)
    board.pan_by(3, 3)

    board.set_grid(12, 12)

    assert board._pan == QPoint(0, 0)


# ---------------------------------------------------------- what went away


def test_the_map_no_longer_grows_and_shrinks_itself(board):
    """How big the room is belongs to the fight, not to a button on its edge."""
    assert not hasattr(board, "_wider")
    assert not hasattr(board, "resize_requested")


def test_the_panel_still_offers_the_view(qtbot, ctx):
    from canon_keeper.panels.encounter.widget import EncounterWidget

    widget = EncounterWidget(ctx)
    qtbot.addWidget(widget)

    labels = [a.label for a in widget.panel_actions()]

    assert "Zoom &in" in labels
    assert "&Whole map" in labels
