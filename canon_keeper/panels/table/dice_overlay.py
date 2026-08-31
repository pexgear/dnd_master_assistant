"""The die that appears when you click a roll the DM asked for.

It tumbles in ASCII, which is a deliberate choice rather than a limitation. A
drawn die would need art that has to be themed, scaled and shipped; a monospace
one is legible on every platform, in both themes, at any font size the reader
has already chosen, and it costs nothing to look at.

The important part is what it does **not** do: it does not decide the number.
The tumbling starts when the request goes to the host and stops when the host's
answer comes back, so the animation is covering a real round trip rather than
decorating a number this process made up. If the answer never arrives the die
says so instead of settling on something plausible.
"""

from __future__ import annotations

import random

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

#: Three silhouettes of the same twenty-sided die, cycled to suggest it turning
#: over. ``{face:^3}`` keeps every frame the same width whatever number is in
#: it, so the die does not jitter as it rolls through 7, 18 and 20.
FRAMES = (
    """
    _________
   /         \\
  /           \\
 /     {face:^3}     \\
 \\             /
  \\           /
   \\_________/
""",
    """
     _______
    /       \\
   /         \\
  /    {face:^3}    \\
  \\           /
   \\         /
    \\_______/
""",
    """
    _________
   /\\       /\\
  /  \\     /  \\
 /    \\{face:^3}/    \\
 \\    /   \\    /
  \\  /     \\  /
   \\/_______\\/
""",
)

#: Fast enough to read as motion, slow enough that the numbers are legible --
#: a die whose face cannot be read is just a flickering rectangle.
TUMBLE_MS = 70

#: How long to wait for the host before admitting nothing came back. Generous:
#: a roll crossing a Tailscale funnel on hotel wifi is still a roll.
PATIENCE_MS = 6000


class AsciiDie(QLabel):
    """A twenty-sided die, tumbling or settled."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setPointSize(11)
        self.setFont(font)

        self._frame = 0
        self._timer = QTimer(self)
        self._timer.setInterval(TUMBLE_MS)
        self._timer.timeout.connect(self._tick)
        self.settle("--")

    @property
    def is_tumbling(self) -> bool:
        return self._timer.isActive()

    def tumble(self) -> None:
        self._timer.start()
        self._tick()

    def settle(self, face) -> None:
        self._timer.stop()
        self.setText(FRAMES[0].format(face=str(face)))

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(FRAMES)
        # Faces are cosmetic while it is in the air, and are thrown away the
        # moment the host says what was actually rolled.
        self.setText(FRAMES[self._frame].format(face=str(random.randint(1, 20))))


class RollDialog(QDialog):
    """One roll the DM asked for, with the die to answer it."""

    #: The notation to ask the host for. Nothing is rolled here.
    roll_requested = Signal(str)

    def __init__(self, label: str, notation: str, bonus_note: str = "",
                 dc: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Roll")
        self._notation = notation
        self._dc = dc

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        heading = QLabel(f"<b>{label}</b>")
        heading.setWordWrap(True)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        self._die = AsciiDie()
        layout.addWidget(self._die)

        self._detail = QLabel(bonus_note or f"Rolling {notation}.")
        self._detail.setWordWrap(True)
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._detail)

        self._roll_button = QPushButton(f"Roll {notation}")
        self._roll_button.setDefault(True)
        self._roll_button.clicked.connect(self._roll)
        layout.addWidget(self._roll_button)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._patience = QTimer(self)
        self._patience.setSingleShot(True)
        self._patience.setInterval(PATIENCE_MS)
        self._patience.timeout.connect(self._gave_up)

    # ------------------------------------------------------------------ rolling

    def _roll(self) -> None:
        self._roll_button.setEnabled(False)
        self._detail.setText("Asking the table's dice...")
        self._die.tumble()
        self._patience.start()
        self.roll_requested.emit(self._notation)

    def settle(self, payload: dict) -> None:
        """The host answered. Stop on the number it actually rolled."""
        if not self._die.is_tumbling:
            return
        self._patience.stop()

        rolls = payload.get("rolls") or []
        total = payload.get("total")
        # The die shows the natural roll -- what a real one would be showing --
        # and the arithmetic is spelled out underneath.
        self._die.settle(rolls[0] if len(rolls) == 1 else total)

        line = str(payload.get("description") or f"{total}")
        if self._dc is not None and isinstance(total, int):
            line += (
                f"  --  that beats DC {self._dc}."
                if total >= self._dc
                else f"  --  DC {self._dc}, so not this time."
            )
        self._detail.setText(line)
        self._roll_button.setText("Roll again")
        self._roll_button.setEnabled(True)

    def _gave_up(self) -> None:
        self._die.settle("?")
        self._detail.setText(
            "No answer from the host, so nothing was rolled. The dice are rolled "
            "there, not here -- try again once you are connected."
        )
        self._roll_button.setEnabled(True)
