"""The dialog for renaming panels.

All three layers in one table, because the interesting part is the relationship
between them: seeing that the party calls it one thing and you call it another
is what makes the precedence obvious without explaining it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

COL_DEFAULT = 0
COL_MINE = 1
COL_PARTY = 2

_ID_ROLE = 256


class RenamePanelsDialog(QDialog):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._names = ctx.names
        self._is_dm = ctx.role == "dm"
        self.setWindowTitle("Panel names")
        self.resize(620, 340)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Leave a box empty to fall back to the column on its left. "
                "Your own name always wins."
                if self._is_dm
                else "Leave your name empty to use whatever the party calls it."
            )
        )

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(
            ["Default", "Your name", "The party calls it"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self._table.horizontalHeader()
        for column in (COL_DEFAULT, COL_MINE, COL_PARTY):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, 1)

        if not self._is_dm:
            note = QLabel(
                "Only whoever runs the campaign can change what the party calls "
                "a panel."
            )
            note.setWordWrap(True)
            layout.addWidget(note)

        reset = QPushButton("Clear my names")
        reset.setToolTip("Go back to whatever the party, or the panel, calls them")
        reset.clicked.connect(self._clear_mine)
        layout.addWidget(reset, alignment=Qt.AlignmentFlag.AlignLeft)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate()

    def _populate(self) -> None:
        panels = self._names.panel_ids
        self._table.setRowCount(len(panels))
        for row, panel_id in enumerate(sorted(panels)):
            default = QTableWidgetItem(self._names.default(panel_id))
            default.setFlags(Qt.ItemFlag.ItemIsEnabled)
            default.setData(_ID_ROLE, panel_id)
            self._table.setItem(row, COL_DEFAULT, default)

            mine = QTableWidgetItem(self._names.local(panel_id))
            mine.setData(_ID_ROLE, panel_id)
            self._table.setItem(row, COL_MINE, mine)

            party = QTableWidgetItem(self._names.party(panel_id))
            party.setData(_ID_ROLE, panel_id)
            if not self._is_dm:
                # A player may see what the DM calls it, but not change it.
                party.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, COL_PARTY, party)

    def _clear_mine(self) -> None:
        for row in range(self._table.rowCount()):
            self._table.item(row, COL_MINE).setText("")

    def apply(self) -> bool:
        """Save the table. Returns True if any party name changed."""
        party_changed = False
        for row in range(self._table.rowCount()):
            panel_id = self._table.item(row, COL_DEFAULT).data(_ID_ROLE)

            mine = self._table.item(row, COL_MINE).text()
            if mine != self._names.local(panel_id):
                self._names.set_local(panel_id, mine)

            if self._is_dm:
                party = self._table.item(row, COL_PARTY).text()
                if party != self._names.party(panel_id):
                    self._names.set_party(panel_id, party)
                    party_changed = True
        return party_changed
