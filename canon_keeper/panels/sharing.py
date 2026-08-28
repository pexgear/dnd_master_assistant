"""The DM's control for "who knows about this".

Shared by the Characters and Cities panels, because sharing a person and sharing
a place are the same decision about the same table.

Deliberately one button showing the current state rather than a row of
checkboxes: mid-scene the DM needs to see at a glance whether the party can see
this thing, and change it in two clicks.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from PySide6.QtCore import Qt

_ACCOUNT_ROLE = 256


def describe(party: bool, account_names: list[str]) -> str:
    if party and account_names:
        return f"Shared with the party (+{len(account_names)})"
    if party:
        return "Shared with the party"
    if len(account_names) == 1:
        return f"Shared with {account_names[0]}"
    if account_names:
        return f"Shared with {len(account_names)} players"
    return "Not shared"


class ShareDialog(QDialog):
    def __init__(self, ctx, entity_id: int, entity_name: str, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._entity_id = entity_id
        self.setWindowTitle(f"Who knows about {entity_name}")

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Players see only the name, the one-liner and what you have "
                "written for them. Your notes, motives and secrets are never sent."
            )
        )

        party, account_ids = ctx.repos.shares.audiences(entity_id)

        self._party = QCheckBox("The whole party")
        self._party.setChecked(party)
        layout.addWidget(self._party)

        layout.addWidget(QLabel("Or particular players:"))
        self._list = QListWidget()
        for account in ctx.repos.accounts.players(ctx.campaign_id):
            label = account.username
            if account.character_entity_id is not None:
                entity = ctx.repos.entities.get(account.character_entity_id)
                if entity is not None:
                    label = f"{account.username}  ({entity.name})"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if account.id in account_ids
                else Qt.CheckState.Unchecked
            )
            item.setData(_ACCOUNT_ROLE, account.id)
            self._list.addItem(item)
        layout.addWidget(self._list, 1)

        if self._list.count() == 0:
            layout.addWidget(
                QLabel("No player logins yet - add them under Table > Players...")
            )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> None:
        chosen = {
            self._list.item(row).data(_ACCOUNT_ROLE)
            for row in range(self._list.count())
            if self._list.item(row).checkState() == Qt.CheckState.Checked
        }
        self._ctx.repos.shares.set_audiences(
            self._ctx.campaign_id, self._entity_id, self._party.isChecked(), chosen
        )


class ShareBar(QPushButton):
    """One button: shows who can see this, and opens the picker."""

    changed = Signal(int)  # entity id

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._entity_id: int | None = None
        self.setToolTip("Choose who can see this")
        self.clicked.connect(self._open)
        self.set_entity(None)

    def set_entity(self, entity_id: int | None) -> None:
        self._entity_id = entity_id
        self.setEnabled(entity_id is not None)
        self.refresh()

    def refresh(self) -> None:
        if self._entity_id is None:
            self.setText("Not shared")
            return
        party, account_ids = self._ctx.repos.shares.audiences(self._entity_id)
        names = []
        for account_id in account_ids:
            account = self._ctx.repos.accounts.get(account_id)
            if account is not None:
                names.append(account.username)
        self.setText(describe(party, sorted(names)))

    def _open(self) -> None:
        if self._entity_id is None:
            return
        entity = self._ctx.repos.entities.get(self._entity_id)
        dialog = ShareDialog(
            self._ctx, self._entity_id, entity.name if entity else "this", self
        )
        if dialog.exec():
            dialog.apply()
            self.refresh()
            # Tells the host to republish, so a revoked share actually
            # disappears from the player's screen rather than going stale.
            self._ctx.bus.share_changed.emit(self._entity_id)
            self.changed.emit(self._entity_id)
