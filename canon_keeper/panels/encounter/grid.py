"""The battle grid: squares, tokens, and dragging one to another square.

Deliberately knows nothing about encounters, repositories or the wire. It is
handed a size and a list of tokens, and it emits what the person did. Both the
DM's map and a player's read-only one are this same widget with ``read_only``
set differently, so the two cannot drift apart in how they draw a fight.

Five feet to the square, like the books, and no pixel coordinates anywhere: a
token is at (3, -2) or it is not on the map at all. Half-squares are a rendering
idea, and reach and range are not rendering questions.

**0,0 is the middle**, x to the right and y downwards, per
:mod:`canon_keeper_protocol.grid`. The rulers along the top and left are what
make that usable out loud: "the one at minus three, two" is a square everyone
can find, and it is still that square after the map grows.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QToolButton, QWidget

from canon_keeper_protocol import grid

#: Below this a token is a coloured dot with no room for initials, which is
#: worse than making the panel scroll.
MIN_CELL = 18
#: Above it the map stops looking like a battlefield and starts looking like a
#: chessboard for giants.
MAX_CELL = 64

#: Room kept clear along the right and bottom edges for the grow/shrink
#: buttons, so they sit beside the grid rather than on top of it.
CONTROL_STRIP = 26

#: Room along the top and left for the coordinate rulers. Everyone gets these,
#: players included: naming a square is how a table talks about a map.
RULER = 22

#: What a combatant being dragged out of the initiative list looks like on the
#: clipboard. Its own type rather than Qt's item-model format, because the map
#: needs one number and parsing an item model to find it would be work in
#: exchange for nothing.
COMBATANT_MIME = "application/x-canonkeeper-combatant"

#: Player characters and everything else. Two hues, not a palette per kind: at
#: a glance the only question is "is that us or them".
_OURS = QColor(70, 130, 200)
_THEIRS = QColor(180, 80, 70)
_TURN = QColor(240, 190, 60)
#: Rock, pillar, overturned cart. Grey on purpose: terrain is scenery, and it
#: must not compete with the two colours that mean "us" and "them".
_STONE = QColor(120, 120, 124)

#: A turn that has been offered and not yet accepted. Distinct from both sides'
#: colours, because the whole point of it is that it has not happened.
_PLAN = QColor(150, 150, 160)
_BLADE = QColor(210, 90, 60)


@dataclass(frozen=True)
class Token:
    """One creature on the map, ready to draw."""

    id: int
    label: str
    x: int
    y: int
    #: True for a player character. Drawn in the party's colour.
    ours: bool = False
    #: Whose turn it is. Ringed, so the answer to "who is up" is visible from
    #: across the table rather than read off a list.
    is_turn: bool = False
    #: DM only: on the map, but not shared, so no player can see it. Drawn
    #: dotted -- the DM should not have to guess which of the two states a
    #: token is in, and asking the party is not a way to find out.
    unseen: bool = False

    @property
    def initials(self) -> str:
        parts = [word for word in self.label.split() if word]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][:1] + parts[1][:1]).upper()


@dataclass(frozen=True)
class Preview:
    """A turn somebody has been offered and has not answered yet.

    Drawn rather than described, because "move to 0,4 and attack the orc" is a
    sentence you have to translate back into the map you are looking at. A
    dotted line and a ghost is the same information with the translation
    already done.
    """

    #: The combatant about to act.
    token: int
    #: Where they would end up, or None if they are staying put.
    to: tuple[int, int] | None = None
    #: The square of whoever they would attack.
    target: tuple[int, int] | None = None


class GridMap(QWidget):
    """A grid of squares with tokens standing on them."""

    #: (combatant id, x, y) -- someone dragged a token onto a square.
    moved = Signal(int, int, int)
    #: A token was clicked. -1 when the click landed on empty floor.
    picked = Signal(int)
    #: An empty square was clicked: (x, y).
    square_clicked = Signal(int, int)
    #: Right-click: (combatant id or -1, global position).
    menu_requested = Signal(int, QPoint)
    #: (combatant id, x, y) -- someone dragged a row of the initiative order
    #: onto a square. The commonest way a token gets onto the map at all.
    dropped = Signal(int, int, int)
    #: (x, y) -- ctrl-click: put something in the way here, or take it away.
    obstacle_toggled = Signal(int, int)
    #: (columns, rows) to add -- negative to take away. The buttons on the edge
    #: of the map itself, because "the room is bigger than that" is a thought
    #: you have while looking at the room.
    resize_requested = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._width = 20
        self._height = 15
        self._tokens: list[Token] = []
        #: Squares nobody may stand in. Terrain, so everyone sees the same ones.
        self._obstacles: set[tuple[int, int]] = set()
        #: A turn on offer, drawn over the map until it is answered.
        self._preview: Preview | None = None
        #: Held while a turn is waiting on an answer: the map still reads
        #: normally -- it is showing the thing being decided -- but it stops
        #: taking clicks, so fiddling with it cannot be mistaken for answering.
        self.frozen = False
        self._read_only = False
        #: How small and how large the grid may get. Set by whoever owns the
        #: data; this widget only needs it to grey the right button out.
        self.limits = (1, 999)
        #: The token being dragged, and where the pointer currently is. Nothing
        #: is committed until the button comes back up, so a drag that ends
        #: somewhere impossible simply does not happen.
        self._dragging: int | None = None
        self._drag_from: tuple[int, int] | None = None
        self._drag_to: tuple[int, int] | None = None
        self._selected: int | None = None
        #: The square something is being dragged over, outlined so the drop
        #: lands where the pointer says it will.
        self._hover: tuple[int, int] | None = None
        self.setMinimumSize(220, 180)
        self.setMouseTracking(False)
        self.setAcceptDrops(True)

        self._wider = self._edge_button("+", "One more column", 1, 0)
        self._narrower = self._edge_button("-", "One column fewer", -1, 0)
        self._taller = self._edge_button("+", "One more row", 0, 1)
        self._shorter = self._edge_button("-", "One row fewer", 0, -1)
        self._arrange_buttons()

    def _edge_button(self, label: str, tip: str, dx: int, dy: int) -> QToolButton:
        button = QToolButton(self)
        button.setText(label)
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.setFixedSize(CONTROL_STRIP - 4, CONTROL_STRIP - 4)
        button.clicked.connect(lambda: self.resize_requested.emit(dx, dy))
        return button

    # ------------------------------------------------------------------ input

    @property
    def read_only(self) -> bool:
        return self._read_only

    @read_only.setter
    def read_only(self, value: bool) -> None:
        self._read_only = bool(value)
        # A player has nothing to press. The strip is reclaimed for the grid
        # rather than left blank, so their map is the bigger one.
        self._arrange_buttons()
        self.update()

    def set_grid(self, width: int, height: int) -> None:
        self._width = max(1, int(width))
        self._height = max(1, int(height))
        self._arrange_buttons()
        self.update()

    def set_tokens(self, tokens: list[Token]) -> None:
        self._tokens = list(tokens)
        # A token that has left the fight cannot stay selected: the next click
        # on an empty square would try to place something that is not there.
        if self._selected is not None and not any(t.id == self._selected for t in tokens):
            self._selected = None
        self.update()

    def set_obstacles(self, squares) -> None:
        self._obstacles = {(int(x), int(y)) for x, y in squares}
        self.update()

    def set_preview(self, preview: Preview | None) -> None:
        self._preview = preview
        self.update()

    def select(self, combatant_id: int | None) -> None:
        self._selected = combatant_id
        self.update()

    @property
    def selected(self) -> int | None:
        return self._selected

    # ---------------------------------------------------------------- drawing

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(RULER + self._width * 28, RULER + self._height * 28)

    def _strip(self) -> int:
        return 0 if self._read_only else CONTROL_STRIP

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return grid.bounds(self._width, self._height)

    def _cell(self) -> int:
        strip = self._strip()
        by_width = (self.width() - strip - RULER) // max(1, self._width)
        by_height = (self.height() - strip - RULER) // max(1, self._height)
        return max(MIN_CELL, min(MAX_CELL, min(by_width, by_height)))

    def _origin(self) -> QPoint:
        """Where the top-left *square* is drawn, in pixels."""
        cell, strip = self._cell(), self._strip()
        spare_x = self.width() - RULER - strip - cell * self._width
        spare_y = self.height() - RULER - strip - cell * self._height
        return QPoint(
            RULER + max(0, spare_x // 2),
            RULER + max(0, spare_y // 2),
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        self._arrange_buttons()

    def _arrange_buttons(self) -> None:
        """Put the four controls on the edges of the board, not the widget.

        On the board, because they are about the grid rather than about the
        panel -- pressing the one at the right-hand edge should feel like
        pushing that wall out.
        """
        for button in (self._wider, self._narrower, self._taller, self._shorter):
            button.setVisible(not self._read_only)
        if self._read_only:
            return

        cell, origin = self._cell(), self._origin()
        right = origin.x() + cell * self._width + 2
        bottom = origin.y() + cell * self._height + 2
        size = self._wider.width()
        middle_y = origin.y() + (cell * self._height - size * 2) // 2
        middle_x = origin.x() + (cell * self._width - size * 2) // 2

        self._narrower.move(right, max(origin.y(), middle_y))
        self._wider.move(right, max(origin.y(), middle_y) + size)
        self._shorter.move(max(origin.x(), middle_x), bottom)
        self._taller.move(max(origin.x(), middle_x) + size, bottom)

        smallest, largest = self.limits
        self._narrower.setEnabled(self._width > smallest)
        self._wider.setEnabled(self._width < largest)
        self._shorter.setEnabled(self._height > smallest)
        self._taller.setEnabled(self._height < largest)

    def _square_at(self, point: QPoint) -> tuple[int, int] | None:
        """Which square a pixel is in, in map coordinates."""
        cell = self._cell()
        origin = self._origin()
        left, top, _right, _bottom = self.bounds
        column = (point.x() - origin.x()) // cell
        row = (point.y() - origin.y()) // cell
        if 0 <= column < self._width and 0 <= row < self._height:
            return int(left + column), int(top + row)
        return None

    def _at(self, x: int, y: int, cell: int, origin: QPoint) -> QRect:
        """The pixels of one square, from its map coordinates."""
        left, top, _right, _bottom = self.bounds
        return QRect(
            origin.x() + (x - left) * cell,
            origin.y() + (y - top) * cell,
            cell,
            cell,
        )

    def _token_at(self, x: int, y: int) -> Token | None:
        for token in self._tokens:
            if token.x == x and token.y == y:
                return token
        return None

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        cell = self._cell()
        origin = self._origin()
        board = QRect(origin.x(), origin.y(), cell * self._width, cell * self._height)

        painter.fillRect(board, palette.base())

        left, top, right, bottom = self.bounds

        # Under the grid lines: terrain is the floor, and lines drawn over it
        # keep the squares countable across a rock the way they are elsewhere.
        for x, y in self._obstacles:
            if left <= x <= right and top <= y <= bottom:
                painter.fillRect(self._at(x, y, cell, origin), _STONE)

        # Lines faint enough to read tokens over. The two through 0,0 are
        # heaviest, because they are what "minus three, two" is counted from;
        # every fifth after that, since five squares is twenty-five feet and
        # that is the distance anyone at a table actually counts.
        text = palette.text().color()
        faint = QColor(text)
        faint.setAlpha(45)
        strong = QColor(text)
        strong.setAlpha(95)
        axis = QColor(text)
        axis.setAlpha(150)

        for column in range(self._width + 1):
            here = left + column
            painter.setPen(
                QPen(axis if here == 0 else strong if here % 5 == 0 else faint)
            )
            x = origin.x() + column * cell
            painter.drawLine(x, board.top(), x, board.bottom())
        for row in range(self._height + 1):
            here = top + row
            painter.setPen(
                QPen(axis if here == 0 else strong if here % 5 == 0 else faint)
            )
            y = origin.y() + row * cell
            painter.drawLine(board.left(), y, board.right(), y)

        self._draw_rulers(painter, cell, origin, board)

        if self._hover is not None:
            painter.setPen(QPen(self.palette().highlight().color(), max(2, cell // 8)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._at(self._hover[0], self._hover[1], cell, origin))

        for token in self._tokens:
            at = self._drag_to if token.id == self._dragging and self._drag_to else (token.x, token.y)
            self._draw_token(painter, token, at[0], at[1], cell, origin)

        self._draw_preview(painter, cell, origin)
        painter.end()

    def _draw_preview(self, painter: QPainter, cell: int, origin: QPoint) -> None:
        """The turn on offer: where they would go, and who they would hit."""
        if self._preview is None:
            return
        actor = next(
            (t for t in self._tokens if t.id == self._preview.token), None
        )
        if actor is None:
            return

        start = self._at(actor.x, actor.y, cell, origin).center()

        if self._preview.to is not None:
            end = self._at(*self._preview.to, cell, origin).center()
            painter.setPen(
                QPen(_PLAN, max(2, cell // 12), Qt.PenStyle.DashLine)
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(start, end)

            ghost = QColor(_OURS if actor.ours else _THEIRS)
            ghost.setAlpha(110)
            box = self._at(*self._preview.to, cell, origin).adjusted(
                cell // 8, cell // 8, -cell // 8, -cell // 8
            )
            painter.setPen(QPen(_PLAN, max(1, cell // 16), Qt.PenStyle.DashLine))
            painter.setBrush(ghost)
            painter.drawEllipse(box)

        if self._preview.target is not None:
            self._draw_sword(
                painter, self._at(*self._preview.target, cell, origin), cell
            )

    @staticmethod
    def _draw_sword(painter: QPainter, square, cell: int) -> None:
        """A sword over whoever is about to be hit.

        Drawn rather than written: at this size a word is unreadable and a
        shape is not, and the only question it has to answer is "that one?".
        """
        pen = QPen(_BLADE, max(2, cell // 9))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        middle = square.center()
        reach = cell // 3
        # The blade, running corner to corner, and a crossguard across it.
        painter.drawLine(
            middle.x() - reach, middle.y() + reach,
            middle.x() + reach, middle.y() - reach,
        )
        guard = cell // 6
        painter.drawLine(
            middle.x() - reach + guard // 2, middle.y() + reach - guard * 2,
            middle.x() - reach + guard * 2, middle.y() + reach - guard // 2,
        )

    def _draw_rulers(
        self, painter: QPainter, cell: int, origin: QPoint, board: QRect
    ) -> None:
        """The numbers along the top and left.

        This is what makes a square something people can say out loud. Every
        square is labelled when there is room; when there is not, every second
        or fifth, and 0 always -- the one anybody counts from.
        """
        left, top, _right, _bottom = self.bounds
        step = 1 if cell >= 30 else 2 if cell >= 20 else 5

        font = QFont(painter.font())
        font.setPixelSize(max(8, min(12, cell // 2)))
        painter.setFont(font)
        ink = QColor(self.palette().text().color())
        ink.setAlpha(190)
        painter.setPen(QPen(ink))

        for column in range(self._width):
            here = left + column
            if here != 0 and here % step:
                continue
            painter.drawText(
                QRect(origin.x() + column * cell, board.top() - RULER, cell, RULER),
                Qt.AlignmentFlag.AlignCenter,
                str(here),
            )
        for row in range(self._height):
            here = top + row
            if here != 0 and here % step:
                continue
            painter.drawText(
                QRect(board.left() - RULER, origin.y() + row * cell, RULER - 3, cell),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(here),
            )

    def _draw_token(
        self, painter: QPainter, token: Token, x: int, y: int, cell: int, origin: QPoint
    ) -> None:
        margin = max(2, cell // 10)
        square = self._at(x, y, cell, origin)
        box = square.adjusted(margin, margin, -margin, -margin)
        fill = QColor(_OURS if token.ours else _THEIRS)
        if token.id == self._dragging:
            fill.setAlpha(150)

        if token.unseen:
            pen = QPen(fill.darker(160), max(1, cell // 14), Qt.PenStyle.DotLine)
        else:
            pen = QPen(fill.darker(160), max(1, cell // 16))
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawEllipse(box)

        if token.is_turn:
            ring = QPen(_TURN, max(2, cell // 9))
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(box.adjusted(-margin, -margin, margin, margin))

        if token.id == self._selected:
            painter.setPen(QPen(self.palette().highlight().color(), max(1, cell // 14)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(square)

        if cell >= MIN_CELL + 4:
            font = QFont(painter.font())
            font.setPixelSize(max(8, cell // 3))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, token.initials)

    # ------------------------------------------------------------------ mouse

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.frozen:
            return
        square = self._square_at(event.position().toPoint())
        token = self._token_at(*square) if square else None

        # Ctrl-click builds the room: something in the way, or no longer. Held
        # rather than moded, because a DM adding one rock should not have to
        # remember to turn a tool off before moving the next goblin.
        if (
            not self.read_only
            and square is not None
            and event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.obstacle_toggled.emit(square[0], square[1])
            return

        if event.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(
                token.id if token else -1, event.globalPosition().toPoint()
            )
            return

        if token is not None:
            self._selected = token.id
            self.picked.emit(token.id)
            if not self.read_only:
                self._dragging = token.id
                self._drag_from = (token.x, token.y)
                self._drag_to = (token.x, token.y)
            self.update()
            return

        self.picked.emit(-1)
        if square is not None:
            self.square_clicked.emit(square[0], square[1])

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self._dragging is None:
            return
        square = self._square_at(event.position().toPoint())
        if square is not None and square != self._drag_to:
            self._drag_to = square
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self._dragging is None:
            return
        combatant_id, target, start = self._dragging, self._drag_to, self._drag_from
        self._dragging = None
        self._drag_to = None
        self._drag_from = None
        square = self._square_at(event.position().toPoint())
        self.update()
        # Released outside the grid: nothing happens. Taking a token off the
        # map is a decision with a menu item, not something a slipped mouse
        # should be able to do in the middle of a fight.
        if square is None or target is None or square != target:
            return
        # A click that selected a token is not a move to where it already is.
        if square == start:
            return
        self.moved.emit(combatant_id, square[0], square[1])

    # ----------------------------------------------------------------- dropping

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self._will_take(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if not self._will_take(event):
            return
        square = self._square_at(event.position().toPoint())
        if square != self._hover:
            self._hover = square
            self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        self._hover = None
        self.update()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt's name
        square = self._square_at(event.position().toPoint())
        self._hover = None
        self.update()
        if not self._will_take(event) or square is None:
            return
        try:
            combatant_id = int(bytes(event.mimeData().data(COMBATANT_MIME)).decode())
        except (TypeError, ValueError):
            return
        event.acceptProposedAction()
        self.dropped.emit(combatant_id, square[0], square[1])

    def _will_take(self, event) -> bool:
        return not self.read_only and event.mimeData().hasFormat(COMBATANT_MIME)
