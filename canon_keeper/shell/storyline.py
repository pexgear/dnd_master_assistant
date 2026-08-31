"""The beats of a one-shot, and where it stops.

A template's storyline is the DM's own notes, so it never leaves this machine —
there is no wire message for it and no panel a player could open. It is a
checklist with the text you might read out, and one beat marked as the ending,
because the difference between a one-shot and a campaign is knowing where to
stop.

Progress is kept in the campaign's settings rather than derived from facts. A
beat being done is the DM's judgement about their own evening, not something the
app can work out by watching.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from canon_keeper.templates import PROGRESS_SETTING, STORYLINE_SETTING, Beat

_ID_ROLE = 256


class StorylineDialog(QDialog):
    """Tick beats off as the evening goes."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle("Storyline")
        self.resize(560, 460)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Your notes for this one-shot. Nothing here is ever sent to a "
                "player."
            )
        )

        self._list = QListWidget()
        self._list.itemChanged.connect(self._on_ticked)
        layout.addWidget(self._list, 1)

        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._detail)
        self._list.currentItemChanged.connect(lambda *_a: self._describe())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._beats = beats_of(ctx)
        self._reload()

    # ------------------------------------------------------------------ loading

    def _reload(self) -> None:
        done = set(self._ctx.repos.settings.get(PROGRESS_SETTING, []) or [])
        self._list.blockSignals(True)
        self._list.clear()
        for beat in self._beats:
            label = beat.title
            if beat.ends_it:
                label += "   — this ends it"
            item = QListWidgetItem(label)
            item.setData(_ID_ROLE, beat.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if beat.id in done else Qt.CheckState.Unchecked
            )
            self._list.addItem(item)
        self._list.blockSignals(False)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _describe(self) -> None:
        item = self._list.currentItem()
        beat = self._beat(item.data(_ID_ROLE)) if item else None
        if beat is None:
            self._detail.setText("")
            return
        parts = []
        if beat.read_aloud:
            parts.append(f"To read or paraphrase:\n{beat.read_aloud}")
        if beat.done_when:
            parts.append(f"Done when: {beat.done_when}")
        self._detail.setText("\n\n".join(parts))

    def _beat(self, beat_id: str) -> Beat | None:
        for beat in self._beats:
            if beat.id == beat_id:
                return beat
        return None

    # ------------------------------------------------------------------ ticking

    def _on_ticked(self, item: QListWidgetItem) -> None:
        done = [
            self._list.item(row).data(_ID_ROLE)
            for row in range(self._list.count())
            if self._list.item(row).checkState() == Qt.CheckState.Checked
        ]
        self._ctx.repos.settings.set(PROGRESS_SETTING, done)

        beat = self._beat(item.data(_ID_ROLE))
        if beat is not None and beat.ends_it and item.checkState() == Qt.CheckState.Checked:
            self._ctx.bus.status_message.emit(
                "That is the ending. File > Start Again puts it back to the "
                "beginning, or File > Keep This One makes it a campaign of "
                "your own."
            )


def beats_of(ctx) -> list[Beat]:
    """The storyline stored on this campaign, if it has one."""
    stored = ctx.repos.settings.get(STORYLINE_SETTING, []) or []
    return [Beat.from_dict(raw) for raw in stored if isinstance(raw, dict)]


def has_storyline(ctx) -> bool:
    return bool(beats_of(ctx))
