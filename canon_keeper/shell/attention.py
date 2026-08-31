"""Highlighting a panel until someone has looked at it.

An update that arrives in a panel you are not looking at may as well not have
arrived. The DM shares a character, a player's hit points change, the agent
answers — and if the relevant dock is behind another tab, nothing tells you.

So a flagged panel's title bar is tinted, and the tint fades away once the panel
has actually been seen. Two decisions worth knowing:

**A visible panel is never flagged.** If you are already looking at it, you have
already seen the change; lighting it up would be telling you something you can
read for yourself.

**Seen means shown, not clicked.** ``QDockWidget.visibilityChanged`` fires when
a tab is raised, when a floating window is restored, and when the dock is
un-hidden — all of which are someone looking at it.

The fade is not decoration. An instant clear is easy to miss if it happens while
you are switching tabs; a second of colour draining away is what tells you
which panel had been waiting.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QObject, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDockWidget

#: How long the tint takes to drain once the panel has been seen.
FADE_MS = 900

#: How strongly a waiting panel is tinted, as an alpha over the theme's accent.
TINT_ALPHA = 90


class Attention(QObject):
    """Tints docks that have something waiting, and fades them when seen."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._docks: dict[str, QDockWidget] = {}
        self._animations: dict[str, QVariantAnimation] = {}
        self._waiting: set[str] = set()
        self._colour = QColor("#3d6ea8")

    # ------------------------------------------------------------------ setup

    def watch(self, panel_id: str, dock: QDockWidget) -> None:
        self._docks[panel_id] = dock
        dock.visibilityChanged.connect(
            lambda shown, pid=panel_id: self._on_visibility(pid, shown)
        )

    def set_colour(self, colour: QColor) -> None:
        """Follow the theme. A tint that ignores dark mode is a bright bar."""
        self._colour = QColor(colour)
        for panel_id in self._waiting:
            self._paint(panel_id, TINT_ALPHA)

    # ----------------------------------------------------------------- flagging

    def flag(self, panel_id: str) -> None:
        """Something arrived for this panel."""
        dock = self._docks.get(panel_id)
        if dock is None:
            return
        if self._is_being_looked_at(dock):
            return

        self._waiting.add(panel_id)
        self._stop(panel_id)
        self._paint(panel_id, TINT_ALPHA)

    def is_waiting(self, panel_id: str) -> bool:
        return panel_id in self._waiting

    def clear(self, panel_id: str) -> None:
        """Seen. Drain the colour rather than snapping it off."""
        if panel_id not in self._waiting:
            return
        self._waiting.discard(panel_id)
        self._stop(panel_id)

        animation = QVariantAnimation(self)
        animation.setStartValue(TINT_ALPHA)
        animation.setEndValue(0)
        animation.setDuration(FADE_MS)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(
            lambda alpha, pid=panel_id: self._paint(pid, int(alpha))
        )
        animation.finished.connect(lambda pid=panel_id: self._paint(pid, 0))
        self._animations[panel_id] = animation
        animation.start()

    # ---------------------------------------------------------------- internals

    @staticmethod
    def _is_being_looked_at(dock: QDockWidget) -> bool:
        """Visible on screen, rather than merely existing.

        `isVisible()` is false for a dock behind another tab, which is exactly
        the case this whole module is for.
        """
        return dock.isVisible() and not dock.visibleRegion().isEmpty()

    def _on_visibility(self, panel_id: str, shown: bool) -> None:
        if shown:
            self.clear(panel_id)

    def _stop(self, panel_id: str) -> None:
        animation = self._animations.pop(panel_id, None)
        if animation is not None:
            animation.stop()

    def _paint(self, panel_id: str, alpha: int) -> None:
        dock = self._docks.get(panel_id)
        if dock is None:
            return
        if alpha <= 0:
            dock.setStyleSheet("")
            return
        tint = QColor(self._colour)
        tint.setAlpha(alpha)
        dock.setStyleSheet(
            "QDockWidget::title {"
            f" background: rgba({tint.red()}, {tint.green()}, {tint.blue()},"
            f" {tint.alpha()});"
            " padding: 4px; }"
        )
