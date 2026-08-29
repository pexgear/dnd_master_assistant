"""The DM's queue of changes players have asked for.

Only build changes arrive here -- level, class, ability scores. Hit points and
conditions never queue, because a player asking permission to take damage would
be absurd and stopping combat to grant it worse.

A proposal made against a sheet that has since moved on is flagged rather than
hidden. The DM may still want it; they just need to know the ground shifted.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

_ID_ROLE = 256


class ApprovalsDialog(QDialog):
    """Approve or refuse what players have asked for."""

    decided = Signal(int, bool)  # proposal id, approved

    def __init__(self, proposals: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Waiting for you")
        self.resize(520, 340)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Players can change their own hit points and conditions freely. "
                "These are changes to what their character is."
            )
        )

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        approve = QPushButton("Approve")
        approve.clicked.connect(lambda: self._decide(True))
        layout.addWidget(approve)

        refuse = QPushButton("Refuse")
        refuse.clicked.connect(lambda: self._decide(False))
        layout.addWidget(refuse)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.set_proposals(proposals)

    def set_proposals(self, proposals: list[dict]) -> None:
        self._list.clear()
        for proposal in proposals:
            text = (
                f"{proposal.get('character', '?')} — {proposal.get('description', '')}"
                f"\n    asked by {proposal.get('who', '?')}"
            )
            if proposal.get("stale"):
                text += "   (the sheet has changed since they asked)"
            item = QListWidgetItem(text)
            item.setData(_ID_ROLE, proposal.get("id"))
            self._list.addItem(item)

        if not proposals:
            empty = QListWidgetItem("Nothing waiting.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(empty)
        else:
            self._list.setCurrentRow(0)

    def _decide(self, approve: bool) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        proposal_id = item.data(_ID_ROLE)
        if proposal_id is None:
            return
        self.decided.emit(int(proposal_id), approve)
        row = self._list.row(item)
        self._list.takeItem(row)
        if self._list.count() == 0:
            self.accept()
