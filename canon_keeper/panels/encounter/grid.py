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

import math
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
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

#: How long each part of an action takes to show. Constants rather than numbers
#: on the wire: every client at a table runs the same build -- the protocol
#: version says so at the door -- so they agree without being told.
STEP_MS = 130      #: one square of walking
LUNGE_MS = 300     #: leaning in for a swing, and back
FLOAT_MS = 1100    #: the damage rising off a token
DOWN_MS = 650      #: a creature going down where it stands
FRAME_MS = 33      #: about thirty a second, which is enough for a token

#: Damage, and the word for when there is none. Red for what it costs; grey for
#: a miss, which is still worth showing -- it is half of what happened.
_HURT = QColor(215, 75, 65)
_MISSED = QColor(150, 150, 155)

#: The three things a creature spends. Green for movement and blue for the
#: action, because they are two halves of one turn and want telling apart at a
#: glance; red for a reaction already spent, because that one is a warning
#: rather than an allowance -- it is what somebody walking past has to know.
_MOVE_LEFT = QColor(110, 190, 120)
_ACTION_LEFT = QColor(120, 170, 230)
_REACTED = QColor(215, 95, 85)

#: How solid a body on the floor is drawn. Faint enough to read as "not in
#: this any more" at a glance, solid enough that nobody has to hunt for it --
#: where somebody fell is a thing the party is trying to get to.
GHOST_OPACITY = 0.35
#: And drained of its side's colour, because a body is not fighting for anybody.
_GHOST = QColor(140, 140, 145)


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
    #: At zero hit points and lying where they fell. Drawn as a ghost: grey,
    #: faint, and still on its square. Taking it away hid the thing a party
    #: most wants to see, which is how far away their friend is.
    down: bool = False
    #: Squares this creature can still walk, and whether its action is still
    #: there. Only meaningful for whoever is up, which is the only token they
    #: are drawn on: what is left of *your* turn is the question a turn is
    #: about, and a pip on every token would be sixteen answers to it.
    squares_left: int = 0
    acted: bool = False
    #: Already swung at somebody walking past this round. The opposite
    #: polarity to the two above, deliberately: still having your reaction is
    #: everybody's default state and drawing it everywhere says nothing, while
    #: having spent it is the exception and it is exactly what somebody
    #: deciding whether to walk past this creature needs to know.
    reacted: bool = False

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


@dataclass(frozen=True)
class Choice:
    """One wedge of the wheel: something this creature could do now."""

    #: "move", or "attack" with a weapon named.
    kind: str
    label: str
    weapon: str = ""


@dataclass
class TurnPlan:
    """What a creature is about to do, as a thing rather than as a side effect.

    Right now each choice is carried out the moment it is picked, and there is
    no going back -- which is the same deal a person gets at a table once the
    die is on the felt. But a turn is a *sequence*, and the difference between
    "do it now" and "line it up and confirm" should be a change to when this is
    handed over, not a rewrite of how the map works.

    So the map builds one of these even though it currently posts it
    immediately. The future -- previewing a whole turn, taking a step back
    before anything is committed -- is a matter of holding it a little longer.
    """

    combatant: int
    #: Where they would end up, if the plan includes moving.
    move: tuple[int, int] | None = None
    #: Who they would hit, and with what.
    target: int | None = None
    weapon: str = ""

    @property
    def is_empty(self) -> bool:
        return self.move is None and self.target is None


#: How big the wheel is, as a fraction of the map's smaller side, and where its
#: ring sits. Kept in one place because a wedge that is drawn and a wedge that
#: is hit-tested disagreeing is the sort of bug nobody sees until they misclick.
RADIAL_INNER = 0.9
RADIAL_OUTER = 2.6


@dataclass
class _Effect:
    """One thing being shown, and how far through it is.

    Time-based rather than frame-based: a laptop that drops frames should show
    a shorter animation, not a slower one, or four people watching the same
    fight fall out of step within a round.
    """

    kind: str
    combatant: int
    duration: float
    started: float = 0.0
    path: list[tuple[int, int]] = field(default_factory=list)
    toward: tuple[int, int] | None = None
    text: str = ""
    colour: QColor = field(default_factory=lambda: QColor(_HURT))

    def progress(self, now: float) -> float:
        if self.duration <= 0:
            return 1.0
        return min(1.0, max(0.0, (now - self.started) / self.duration))

    def done(self, now: float) -> bool:
        return self.progress(now) >= 1.0


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
    #: A turn somebody lined up on the map: see :class:`TurnPlan`. Emitted when
    #: it is complete -- a move with a square, an attack with a target.
    planned = Signal(object)
    #: The wheel opened or closed, so a panel can say what is being asked.
    radial_changed = Signal(bool)
    #: Space was pressed on a token. The panel knows what that creature can do
    #: -- its weapons come off a sheet this widget has never seen -- so it
    #: answers with :meth:`offer`.
    radial_wanted = Signal(int)

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
        #: The wheel, when it is open: the combatant it belongs to and what it
        #: is offering. None when it is not.
        self._radial: int | None = None
        self._choices: list[Choice] = []
        #: Which wedge the mouse is over, so the wheel answers the pointer.
        self._hovering: int | None = None
        #: What the map is waiting for after a wedge was picked: "move" for a
        #: square, "attack" for a creature, or None.
        self._awaiting: Choice | None = None
        #: The square something is being dragged over, outlined so the drop
        #: lands where the pointer says it will.
        self._hover: tuple[int, int] | None = None
        self.setMinimumSize(220, 180)
        self.setMouseTracking(False)
        self.setAcceptDrops(True)
        #: The map takes the keyboard, because the wheel opens on Space and the
        #: whole idea is that your attention never leaves the battlefield. Click
        #: focus rather than tab focus: nobody tabs their way onto a map.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        #: What is being shown right now. Empty almost always, and the timer
        #: only runs while it is not.
        self._effects: list[_Effect] = []
        #: Tokens the state has already dropped but that are still being shown
        #: leaving. Cleared when their effect ends.
        self._leaving: dict[int, Token] = {}
        self._frames = QTimer(self)
        self._frames.setInterval(FRAME_MS)
        self._frames.timeout.connect(self._next_frame)

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

    # ------------------------------------------------------------- showing it

    def play(self, event: dict) -> None:
        """Show what the host says happened.

        The host describes it -- the whole walk, whether the swing landed, how
        much it cost -- so this only has to draw it. Working the same thing out
        from two states would give every screen its own version of the fight,
        started at its own moment.
        """
        kind = str(event.get("kind", ""))
        combatant = event.get("combatant")
        if not isinstance(combatant, int):
            return

        if kind == "move":
            path = [
                (int(square[0]), int(square[1]))
                for square in event.get("path") or ()
                if isinstance(square, (list, tuple)) and len(square) == 2
            ]
            if len(path) > 1:
                self._begin(
                    _Effect(
                        kind="move",
                        combatant=combatant,
                        duration=STEP_MS * (len(path) - 1) / 1000,
                        path=path,
                    )
                )
        elif kind == "attack":
            hit = bool(event.get("hit"))
            damage = int(event.get("damage") or 0)
            target = event.get("target")
            self._begin(
                _Effect(
                    kind="lunge",
                    combatant=combatant,
                    duration=LUNGE_MS / 1000,
                    toward=self._square_of(target),
                )
            )
            if isinstance(target, int):
                self._begin(
                    _Effect(
                        kind="float",
                        combatant=target,
                        duration=FLOAT_MS / 1000,
                        text=f"-{damage}" if hit and damage else "miss",
                        colour=QColor(_HURT if hit and damage else _MISSED),
                    )
                )
        elif kind == "down":
            # They stay on the map now, so this is a fall rather than an exit:
            # the token sinks and fades to a ghost and then stops there. Kept in
            # `_leaving` all the same, for the one case where they really do go
            # -- a DM taking a body out of the fight while it is still falling.
            for token in self._tokens:
                if token.id == combatant:
                    self._leaving[combatant] = token
                    break
            self._begin(
                _Effect(kind="down", combatant=combatant, duration=DOWN_MS / 1000)
            )

    def _begin(self, effect: _Effect) -> None:
        effect.started = time.monotonic()
        # One of each kind per token: a second walk replaces the first rather
        # than drawing the token in two places at once.
        self._effects = [
            other
            for other in self._effects
            if not (other.kind == effect.kind and other.combatant == effect.combatant)
        ]
        self._effects.append(effect)
        if not self._frames.isActive():
            self._frames.start()
        self.update()

    def _next_frame(self) -> None:
        now = time.monotonic()
        self._effects = [effect for effect in self._effects if not effect.done(now)]
        still_going = {effect.combatant for effect in self._effects}
        self._leaving = {
            combatant: token
            for combatant, token in self._leaving.items()
            if combatant in still_going
        }
        if not self._effects:
            self._frames.stop()
        self.update()

    def _drawable(self) -> list[Token]:
        """What to draw: what is there, plus what is still on its way out."""
        showing = {token.id for token in self._tokens}
        return self._tokens + [
            token for combatant, token in self._leaving.items() if combatant not in showing
        ]

    def _effect_on(self, combatant_id: int, kind: str) -> _Effect | None:
        for effect in self._effects:
            if effect.combatant == combatant_id and effect.kind == kind:
                return effect
        return None

    def _square_of(self, combatant_id) -> tuple[int, int] | None:
        for token in self._drawable():
            if token.id == combatant_id:
                return token.x, token.y
        return None


    # ------------------------------------------------------------- the wheel
    #
    # Space opens a ring of choices around whoever is up. The point is that
    # your hand is already on the map: the alternative is a dialog somewhere
    # else, which means looking away from the thing you are deciding about.

    def offer(self, combatant_id: int, choices: list[Choice]) -> None:
        """Open the wheel around one token. No choices means no wheel."""
        if not choices or not any(t.id == combatant_id for t in self._drawable()):
            return
        self._radial = combatant_id
        self._choices = list(choices)
        self._hovering = None
        self._awaiting = None
        # Only while the wheel is up: the rest of the time a move event per
        # pixel is work for nothing.
        self.setMouseTracking(True)
        self.radial_changed.emit(True)
        self.update()

    def close_radial(self) -> None:
        was = self._radial is not None or self._awaiting is not None
        self._radial = None
        self._choices = []
        self._hovering = None
        self._awaiting = None
        self.setMouseTracking(False)
        if was:
            self.radial_changed.emit(False)
        self.update()

    @property
    def radial_open(self) -> bool:
        return self._radial is not None

    @property
    def awaiting(self) -> Choice | None:
        """What the map is waiting to be pointed at, having been asked."""
        return self._awaiting

    def _radial_centre(self, cell: int, origin: QPoint) -> QPoint | None:
        for token in self._drawable():
            if token.id == self._radial:
                square = self._at(token.x, token.y, cell, origin)
                return square.center()
        return None

    def _wedge_at(self, point: QPoint, cell: int, origin: QPoint) -> int | None:
        """Which wedge the point falls in, or None for outside the ring."""
        centre = self._radial_centre(cell, origin)
        if centre is None or not self._choices:
            return None
        dx = point.x() - centre.x()
        dy = point.y() - centre.y()
        away = math.hypot(dx, dy)
        if not (cell * RADIAL_INNER <= away <= cell * RADIAL_OUTER):
            return None
        # Straight up is the first wedge, and they run clockwise, because that
        # is the order the labels are read in.
        angle = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
        return int(angle // (360.0 / len(self._choices)))

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

        now = time.monotonic()
        for token in self._drawable():
            at = self._drag_to if token.id == self._dragging and self._drag_to else None
            self._draw_token(painter, token, at, cell, origin, now)

        self._draw_preview(painter, cell, origin)
        self._draw_radial(painter, cell, origin, now)
        self._draw_numbers(painter, cell, origin, now)
        painter.end()

    def _draw_radial(
        self, painter: QPainter, cell: int, origin: QPoint, now: float
    ) -> None:
        """The wheel of choices, around whoever it belongs to.

        Drawn over the map on purpose. It covers a few squares while it is
        open, and it is open for about a second: the alternative is putting the
        choices somewhere with room for them, which means somewhere that is not
        where you are looking.
        """
        centre = self._radial_centre(cell, origin)
        if centre is None or not self._choices:
            return

        inner = cell * RADIAL_INNER
        outer = cell * RADIAL_OUTER
        box = QRect(
            int(centre.x() - outer),
            int(centre.y() - outer),
            int(outer * 2),
            int(outer * 2),
        )
        hole = QRect(
            int(centre.x() - inner),
            int(centre.y() - inner),
            int(inner * 2),
            int(inner * 2),
        )
        span = 360.0 / len(self._choices)

        palette = self.palette()
        font = QFont(painter.font())
        font.setPixelSize(max(9, min(14, cell // 3)))
        painter.setFont(font)
        metrics = painter.fontMetrics()

        for index, choice in enumerate(self._choices):
            # Qt measures from three o'clock anticlockwise in sixteenths of a
            # degree; the wheel reads from twelve o'clock clockwise. Hence 90.
            start = 90.0 - (index + 1) * span
            fill = QColor(palette.window().color())
            fill.setAlpha(235)
            if index == self._hovering:
                fill = QColor(palette.highlight().color())
                fill.setAlpha(235)
            painter.setBrush(fill)
            painter.setPen(QPen(QColor(palette.text().color()), 1))
            painter.drawPie(box, int(start * 16), int(span * 16) - 12)

            middle = math.radians(start + span / 2.0)
            reach = (inner + outer) / 2.0
            label = metrics.elidedText(
                choice.label,
                Qt.TextElideMode.ElideRight,
                int((outer - inner) * 1.6),
            )
            where = QPoint(
                int(centre.x() + math.cos(middle) * reach),
                int(centre.y() - math.sin(middle) * reach),
            )
            painter.setPen(
                QPen(
                    palette.highlightedText().color()
                    if index == self._hovering
                    else palette.text().color()
                )
            )
            painter.drawText(
                QRect(where.x() - 60, where.y() - 10, 120, 20),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

        # Punch the token back out of the middle, so the wheel never hides the
        # creature it belongs to: whose turn this is has to stay readable while
        # you are deciding what they do.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(palette.base())
        painter.drawEllipse(hole)
        for token in self._drawable():
            if token.id == self._radial:
                self._draw_token(painter, token, None, cell, origin, now)
                break

    def _draw_numbers(
        self, painter: QPainter, cell: int, origin: QPoint, now: float
    ) -> None:
        """Damage rising off whoever took it, and fading.

        Drawn last, over everything: it is the one thing on the map that is
        about a moment rather than a state, and it has a second to be read.
        """
        font = QFont(painter.font())
        font.setPixelSize(max(11, cell // 2))
        font.setBold(True)
        painter.setFont(font)

        for effect in self._effects:
            if effect.kind != "float":
                continue
            square = self._square_of(effect.combatant) or effect.toward
            if square is None:
                continue
            done = effect.progress(now)
            box = self._at(square[0], square[1], cell, origin)
            colour = QColor(effect.colour)
            # Rises a square's height over its life, and fades over the last
            # third, so it is readable before it starts going.
            colour.setAlpha(int(255 * min(1.0, (1.0 - done) * 3)))
            painter.setPen(QPen(colour))
            painter.drawText(
                box.translated(0, int(-cell * done)),
                Qt.AlignmentFlag.AlignCenter,
                effect.text,
            )

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

    def _where_to_draw(
        self, token: Token, cell: int, origin: QPoint, now: float
    ) -> tuple[QRect, float]:
        """The token's square right now, and how solid it is.

        While something is being shown for a token, the animation owns where it
        is drawn -- the state underneath has already moved on, and drawing from
        that would put it at its destination before it has walked there.
        """
        square = self._at(token.x, token.y, cell, origin)
        opacity = 1.0

        walking = self._effect_on(token.id, "move")
        if walking and len(walking.path) > 1:
            done = walking.progress(now)
            steps = len(walking.path) - 1
            exact = done * steps
            index = min(steps - 1, int(exact))
            start = self._at(*walking.path[index], cell, origin)
            end = self._at(*walking.path[index + 1], cell, origin)
            between = exact - index
            square = QRect(
                int(start.x() + (end.x() - start.x()) * between),
                int(start.y() + (end.y() - start.y()) * between),
                cell,
                cell,
            )

        lunging = self._effect_on(token.id, "lunge")
        if lunging and lunging.toward is not None:
            done = lunging.progress(now)
            # Out and back, so it reads as a swing rather than a step.
            reach = (1.0 - abs(done * 2 - 1.0)) * 0.4
            target = self._at(*lunging.toward, cell, origin)
            square = square.translated(
                int((target.x() - square.x()) * reach),
                int((target.y() - square.y()) * reach),
            )

        falling = self._effect_on(token.id, "down")
        if falling:
            # Down to the ghost and no further. A token that faded to nothing
            # would say "gone", and they are not gone -- they are on the floor,
            # on that square, and somebody can reach them.
            done = falling.progress(now)
            opacity = 1.0 - done * (1.0 - GHOST_OPACITY)
            shrink = int(cell * done * 0.2)
            square = square.adjusted(shrink, shrink, -shrink, -shrink)
        elif token.down:
            opacity = GHOST_OPACITY
            shrink = int(cell * 0.2)
            square = square.adjusted(shrink, shrink, -shrink, -shrink)

        return square, opacity

    def _draw_token(
        self,
        painter: QPainter,
        token: Token,
        dragged_to: tuple[int, int] | None,
        cell: int,
        origin: QPoint,
        now: float,
    ) -> None:
        if dragged_to is not None:
            square = self._at(dragged_to[0], dragged_to[1], cell, origin)
            opacity = 1.0
        else:
            square, opacity = self._where_to_draw(token, cell, origin, now)

        margin = max(2, cell // 10)
        box = square.adjusted(margin, margin, -margin, -margin)
        going_down = self._effect_on(token.id, "down") is not None
        fill = QColor(
            _GHOST if (token.down or going_down)
            else (_OURS if token.ours else _THEIRS)
        )
        fill.setAlphaF(fill.alphaF() * opacity)
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
            initials = QColor(255, 255, 255)
            initials.setAlphaF(opacity)
            painter.setPen(QPen(initials))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, token.initials)

        if not (token.down or going_down):
            self._draw_budget(painter, token, square, cell, opacity)

    def _draw_budget(
        self, painter: QPainter, token: Token, square: QRect, cell: int, opacity: float
    ) -> None:
        """What this creature has left, under its feet.

        Two questions, and they want opposite answers. *What is left of my
        turn* is asked about one creature -- whoever is up -- so its two pips
        are drawn only there; a boot and a blade on every token would be
        sixteen answers to a question about one of them. *Can that thing swing
        at me as I go past* is asked about everybody else, and its answer is
        interesting only when it is no: a mark that appears once the reaction
        is spent, rather than one that sits on every token saying "still".

        Beneath the token rather than over it, so nothing covers the initials
        that say who this is.
        """
        if cell < MIN_CELL + 6:
            return  # no room for a pip that anybody could tell from a speck

        size = max(5, cell // 5)
        gap = max(2, size // 3)
        marks: list[tuple[QColor, bool]] = []
        if token.is_turn:
            # Filled while it is still there, hollow once it is gone: a spent
            # move should read as an outline of the thing you no longer have.
            marks.append((_MOVE_LEFT, token.squares_left > 0))
            marks.append((_ACTION_LEFT, not token.acted))
        if token.reacted:
            marks.append((_REACTED, True))
        if not marks:
            return

        width = len(marks) * size + (len(marks) - 1) * gap
        x = square.center().x() - width // 2
        # Straddling the bottom of the token, like a badge. Wholly inside it
        # and the initials fight for the space; wholly below and it lands in
        # the next creature's square.
        y = square.bottom() - size

        # A ring in the board's own colour behind every pip, so green on blue
        # and red on red are both still a pip rather than a smudge.
        backing = QPen(self.palette().base().color(), max(1, size // 3))
        for colour, filled in marks:
            shade = QColor(colour)
            shade.setAlphaF(shade.alphaF() * opacity)
            spot = QRect(x, y, size, size)
            painter.setPen(backing)
            painter.setBrush(shade if filled else self.palette().base())
            painter.drawEllipse(spot)
            if not filled:
                # Spent: an outline where the thing used to be.
                painter.setPen(QPen(shade, max(1, size // 4)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(spot.adjusted(1, 1, -1, -1))
            x += size + gap

    # ------------------------------------------------------------------ mouse


    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Space opens the wheel on whoever is selected; Escape puts it away.

        Only for the map that can act. A player's copy is read-only, and a
        wheel that offered choices it could not carry out would be a worse lie
        than no wheel.
        """
        if event.key() == Qt.Key.Key_Escape:
            self.close_radial()
            return
        if event.key() == Qt.Key.Key_Space and not self.read_only:
            if self._radial is not None or self._awaiting is not None:
                self.close_radial()
            elif self._selected is not None:
                self.radial_wanted.emit(self._selected)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.frozen:
            return
        square = self._square_at(event.position().toPoint())
        token = self._token_at(*square) if square else None

        # The wheel gets the click before the board does, so picking a wedge
        # over a token does not also select that token.
        if self._radial is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                chosen = self._wedge_at(
                    event.position().toPoint(), self._cell(), self._origin()
                )
                if chosen is not None:
                    self._picked_wedge(self._choices[chosen])
                    return
            self.close_radial()
            return

        # Having been asked for a square or a creature, the next click answers.
        if self._awaiting is not None and event.button() == Qt.MouseButton.LeftButton:
            self._answer_with(square, token)
            return

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


    def _picked_wedge(self, choice: Choice) -> None:
        """A wedge was chosen. Now the map waits to be pointed at something."""
        self._radial = None
        self._choices = []
        self._hovering = None
        self._awaiting = choice
        self.setMouseTracking(False)
        self.radial_changed.emit(False)
        self.update()

    def _answer_with(self, square, token) -> None:
        """The click that completes what a wedge started.

        Nothing is asked twice: a plan goes out, and whether it is carried out
        immediately or held for confirmation is the panel's to decide. Clicking
        nothing useful puts the wheel away rather than leaving the map in a
        mode somebody has to guess their way out of.
        """
        choice, combatant = self._awaiting, self._selected
        self._awaiting = None
        if choice is None or combatant is None:
            self.update()
            return

        if choice.kind == "move" and square is not None:
            self.planned.emit(TurnPlan(combatant=combatant, move=square))
        elif choice.kind == "attack" and token is not None and token.id != combatant:
            self.planned.emit(
                TurnPlan(combatant=combatant, target=token.id, weapon=choice.weapon)
            )
        self.radial_changed.emit(False)
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self._radial is not None:
            over = self._wedge_at(
                event.position().toPoint(), self._cell(), self._origin()
            )
            if over != self._hovering:
                self._hovering = over
                self.update()
            return
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
