"""Host, join, and manage who may log in.

The join dialog listens for LAN beacons and lists what it hears, so on a home
network nobody has to read an IP address aloud -- pick the session, then log in.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from canon_keeper import campaigns, credentials
from canon_keeper.net import discovery
from canon_keeper.net.auth import MIN_PASSWORD_LENGTH, AuthError
from canon_keeper.net.server import DEFAULT_PORT
from canon_keeper.repo.entities import KIND_PC

_URL_ROLE = 256


def local_addresses() -> list[str]:
    """This machine's LAN addresses, for telling players what to type."""
    return sorted(a for a in discovery.local_addresses() if not a.startswith("127."))


class HostDialog(QDialog):
    """Start hosting the currently open campaign."""

    def __init__(self, campaign_name: str, default_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Host a session")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit(default_name)
        form.addRow("Your name", self._name)

        self._session = QLineEdit(campaign_name)
        form.addRow("Session name", self._session)

        self._port = QSpinBox()
        self._port.setRange(1024, 65535)
        self._port.setValue(DEFAULT_PORT)
        form.addRow("Port", self._port)
        layout.addLayout(form)

        addresses = local_addresses()
        hint = QLabel(
            f"Sharing the campaign {campaign_name!r}. Players on this network will "
            "see the session listed and log in with the accounts you gave them.\n"
            + (f"If they need to type it: {', '.join(addresses)}" if addresses else "")
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, int, str]:
        return (
            self._name.text().strip() or "Dungeon Master",
            self._port.value(),
            self._session.text().strip() or "Canon Keeper session",
        )


class JoinDialog(QDialog):
    def __init__(self, last_url: str = "", last_username: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Join a session")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sessions on this network:"))

        self._found = QListWidget()
        self._found.setMaximumHeight(110)
        self._found.itemSelectionChanged.connect(self._use_selected)
        layout.addWidget(self._found)

        form = QFormLayout()
        self._url = QLineEdit(last_url or "ws://192.168.1.10:8765")
        self._url.setToolTip("Filled in for you when you pick a session above.")
        form.addRow("Address", self._url)

        self._username = QLineEdit(last_username)
        form.addRow("Username", self._username)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.returnPressed.connect(self.accept)
        form.addRow("Password", self._password)
        layout.addLayout(form)

        self._remember = QCheckBox("Remember my password for this session")
        self._remember.setEnabled(credentials.is_available())
        if not credentials.is_available():
            self._remember.setToolTip(
                "This machine has no credential store, so passwords cannot be saved."
            )
        layout.addWidget(self._remember)

        note = QLabel(
            "Your password is never sent over the network -- the host asks a "
            "question only someone who knows it can answer. If saved, it goes to "
            "this computer's credential store."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._listener = discovery.Listener(self)
        self._listener.sessions_changed.connect(self._on_sessions)
        if not self._listener.start():
            self._found.addItem(
                QListWidgetItem("(could not listen for sessions - type the address)")
            )

        self._load_saved()

    def _load_saved(self) -> None:
        """Fill in the password for a session already joined once."""
        url, username = self._url.text().strip(), self._username.text().strip()
        if not url or not username:
            return
        saved = credentials.load(url, username)
        if saved:
            self._password.setText(saved)
            self._remember.setChecked(True)

    def _on_sessions(self, sessions) -> None:
        selected = self._found.currentItem()
        selected_url = selected.data(_URL_ROLE) if selected is not None else None

        self._found.clear()
        for session in sessions:
            item = QListWidgetItem(f"{session.name}  -  {session.host}:{session.port}")
            item.setData(_URL_ROLE, session.url)
            self._found.addItem(item)
            if session.url == selected_url:
                self._found.setCurrentItem(item)

        if not sessions:
            self._found.addItem(QListWidgetItem("(looking for sessions...)"))

    def _use_selected(self) -> None:
        item = self._found.currentItem()
        if item is not None and item.data(_URL_ROLE):
            self._url.setText(item.data(_URL_ROLE))
            for remembered in campaigns.list_remote():
                if remembered.url == item.data(_URL_ROLE) and remembered.username:
                    self._username.setText(remembered.username)
                    break
            self._load_saved()

    def values(self) -> tuple[str, str, str]:
        return (
            self._url.text().strip(),
            self._username.text().strip(),
            self._password.text(),
        )

    def should_remember(self) -> bool:
        return self._remember.isChecked()

    def done(self, result: int) -> None:  # noqa: D102 - Qt naming
        self._listener.stop()
        super().done(result)


class AccountsDialog(QDialog):
    """Who may log in, and which character each of them plays."""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle("Players")
        self.resize(520, 380)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("People with a login here can join your sessions.")
        )

        self._list = QListWidget()
        self._list.currentItemChanged.connect(lambda *_: self._load_selected())
        layout.addWidget(self._list, 1)

        form = QFormLayout()
        self._username = QLineEdit()
        self._username.setPlaceholderText("marco")
        form.addRow("Username", self._username)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText(f"at least {MIN_PASSWORD_LENGTH} characters")
        form.addRow("Password", self._password)

        self._character = QComboBox()
        self._character.setToolTip(
            "The player edits this character, and the table sees its name in chat."
        )
        form.addRow("Plays", self._character)

        self._is_dm = QCheckBox("Give this login the DM's full view")
        form.addRow("", self._is_dm)
        layout.addLayout(form)

        row = QHBoxLayout()
        add = QPushButton("Add / update")
        add.clicked.connect(self._save)
        row.addWidget(add)

        reset = QPushButton("Set password")
        reset.clicked.connect(self._set_password)
        row.addWidget(reset)

        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._reload()

    # ------------------------------------------------------------------ helpers

    def _reload(self) -> None:
        self._character.clear()
        self._character.addItem("(no character)", None)
        for pc in self._ctx.repos.entities.list(self._ctx.campaign_id, kinds=(KIND_PC,)):
            self._character.addItem(pc.name, pc.id)

        self._list.clear()
        for account in self._ctx.repos.accounts.list(self._ctx.campaign_id):
            character = ""
            if account.character_entity_id is not None:
                entity = self._ctx.repos.entities.get(account.character_entity_id)
                character = f"  -  {entity.name}" if entity else ""
            label = f"{account.username}{character}"
            if account.is_dm:
                label += "   [DM]"
            item = QListWidgetItem(label)
            item.setData(_URL_ROLE, account.id)
            self._list.addItem(item)

    def _selected_account(self):
        item = self._list.currentItem()
        if item is None:
            return None
        return self._ctx.repos.accounts.get(item.data(_URL_ROLE))

    def _load_selected(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        self._username.setText(account.username)
        self._password.clear()
        index = self._character.findData(account.character_entity_id)
        self._character.setCurrentIndex(index if index >= 0 else 0)
        self._is_dm.setChecked(account.is_dm)

    # ------------------------------------------------------------------ actions

    def _save(self) -> None:
        username = self._username.text().strip()
        if not username:
            QMessageBox.warning(self, "Players", "A username is needed.")
            return

        repos = self._ctx.repos
        existing = repos.accounts.by_username(self._ctx.campaign_id, username)
        role = "dm" if self._is_dm.isChecked() else "player"

        try:
            if existing is None:
                repos.accounts.create(
                    self._ctx.campaign_id,
                    username,
                    self._password.text(),
                    role=role,
                    character_entity_id=self._character.currentData(),
                )
            else:
                repos.accounts.set_character(existing.id, self._character.currentData())
                if self._password.text():
                    repos.accounts.set_password(existing.id, self._password.text())
        except (ValueError, AuthError) as exc:
            QMessageBox.warning(self, "Players", str(exc))
            return

        self._password.clear()
        self._reload()

    def _set_password(self) -> None:
        account = self._selected_account()
        if account is None:
            QMessageBox.information(self, "Players", "Pick someone first.")
            return
        if not self._password.text():
            QMessageBox.information(self, "Players", "Type the new password first.")
            return
        try:
            self._ctx.repos.accounts.set_password(account.id, self._password.text())
        except AuthError as exc:
            QMessageBox.warning(self, "Players", str(exc))
            return
        self._password.clear()
        QMessageBox.information(self, "Players", f"Password changed for {account.username}.")

    def _remove(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        confirm = QMessageBox.question(
            self, "Players", f"Remove the login {account.username!r}?"
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self._ctx.repos.accounts.delete(account.id)
            self._reload()
