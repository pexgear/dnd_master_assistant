"""Host and join dialogs.

The join dialog listens for LAN beacons and lists what it hears, so on a home
network nobody has to read an IP address aloud -- pick the session, type the
code.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
)

from canon_keeper.net import discovery
from canon_keeper.net.protocol import CODE_LENGTH
from canon_keeper.net.server import DEFAULT_PORT


def local_addresses() -> list[str]:
    """This machine's LAN addresses, for telling players what to type."""
    return sorted(a for a in discovery.local_addresses() if not a.startswith("127."))


class HostDialog(QDialog):
    def __init__(self, default_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Host a session")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit(default_name)
        form.addRow("Your name", self._name)

        self._session = QLineEdit("Our campaign")
        form.addRow("Session name", self._session)

        self._port = QSpinBox()
        self._port.setRange(1024, 65535)
        self._port.setValue(DEFAULT_PORT)
        form.addRow("Port", self._port)
        layout.addLayout(form)

        addresses = local_addresses()
        hint = (
            "Players on this network will see the session listed automatically.\n"
            + (f"If they need to type it: {', '.join(addresses)}" if addresses else "")
        )
        label = QLabel(hint)
        label.setWordWrap(True)
        layout.addWidget(label)

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
    def __init__(self, default_name: str, last_url: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Join a session")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sessions on this network:"))

        self._found = QListWidget()
        self._found.setMaximumHeight(110)
        self._found.itemSelectionChanged.connect(self._use_selected)
        layout.addWidget(self._found)

        form = QFormLayout()
        self._name = QLineEdit(default_name)
        form.addRow("Your name", self._name)

        self._url = QLineEdit(last_url or "ws://192.168.1.10:8765")
        self._url.setToolTip("Filled in for you when you pick a session above.")
        form.addRow("Address", self._url)

        self._code = QLineEdit()
        self._code.setMaxLength(CODE_LENGTH + 2)  # room for a typed dash
        self._code.setPlaceholderText("ABC234")
        form.addRow("Join code", self._code)
        layout.addLayout(form)

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

    def _on_sessions(self, sessions) -> None:
        selected = self._found.currentItem()
        selected_url = selected.data(256) if selected is not None else None

        self._found.clear()
        for session in sessions:
            item = QListWidgetItem(f"{session.name}  -  {session.host}:{session.port}")
            item.setData(256, session.url)
            self._found.addItem(item)
            if session.url == selected_url:
                self._found.setCurrentItem(item)

        if not sessions:
            self._found.addItem(QListWidgetItem("(looking for sessions...)"))

    def _use_selected(self) -> None:
        item = self._found.currentItem()
        if item is not None and item.data(256):
            self._url.setText(item.data(256))

    def values(self) -> tuple[str, str, str]:
        from canon_keeper.net.protocol import normalise_code

        return (
            self._url.text().strip(),
            normalise_code(self._code.text()),
            self._name.text().strip() or "Player",
        )

    def done(self, result: int) -> None:  # noqa: D102 - Qt naming
        self._listener.stop()
        super().done(result)
