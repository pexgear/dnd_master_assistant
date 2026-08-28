"""Choosing a campaign, before anything else opens.

Nothing in the app means much without one: the Characters panel would be a list
of nobody, and a session has no canon to serve. So this is the first thing you
see, and the main window is not built until it returns.

Two ways in. A campaign on this computer is yours -- you hold the file, so there
is nothing to log in to. A campaign on someone else's machine needs the username
and password the DM gave you.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from canon_keeper import campaigns, credentials
from canon_keeper.net import discovery

LOCAL_TAB = 0
ONLINE_TAB = 1

_DATA_ROLE = 256


@dataclass(slots=True)
class Launch:
    """What the app should do once the dialog closes."""

    kind: str  # "local" or "remote"
    path: Path | None = None
    name: str = ""
    url: str = ""
    username: str = ""
    password: str = ""
    #: Keep the password in the OS credential store for next time.
    remember: bool = False
    #: Open this campaign on launch without asking again.
    autostart: bool = False

    @property
    def is_remote(self) -> bool:
        return self.kind == "remote"

    @property
    def role(self) -> str:
        # Holding the campaign file makes you the DM of it. Joining someone
        # else's makes you a player, unless their server says otherwise when
        # you log in.
        return "player" if self.is_remote else "dm"


class CampaignDialog(QDialog):
    def __init__(self, start_online: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Canon Keeper")
        self.resize(560, 420)
        self._launch: Launch | None = None

        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_local_tab(), "On this computer")
        self._tabs.addTab(self._build_online_tab(), "Join a session")
        self._tabs.currentChanged.connect(self._update_buttons)
        layout.addWidget(self._tabs, 1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._reload_local()
        self._reload_remote()
        self._sync_local_autostart()
        if start_online:
            self._tabs.setCurrentIndex(ONLINE_TAB)
        self._update_buttons()

    # ----------------------------------------------------------------- local

    def _build_local_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Campaigns you run:"))

        self._local_list = QListWidget()
        self._local_list.itemDoubleClicked.connect(lambda _i: self._accept())
        layout.addWidget(self._local_list, 1)

        row = QHBoxLayout()
        new_button = QPushButton("New campaign...")
        new_button.clicked.connect(self._new_campaign)
        row.addWidget(new_button)

        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(self._delete_campaign)
        row.addWidget(delete_button)
        row.addStretch(1)
        layout.addLayout(row)

        self._autostart_local = QCheckBox("Open this automatically next time")
        layout.addWidget(self._autostart_local)
        self._local_list.itemSelectionChanged.connect(self._sync_local_autostart)
        return page

    def _sync_local_autostart(self) -> None:
        """Show whether the highlighted campaign is the one that opens on launch."""
        item = self._local_list.currentItem()
        entry = campaigns.get_autostart()
        self._autostart_local.setChecked(
            item is not None
            and entry is not None
            and entry.kind == "local"
            and entry.path == item.data(_DATA_ROLE)
        )

    def _reload_local(self) -> None:
        self._local_list.clear()
        for campaign in campaigns.list_local():
            item = QListWidgetItem(f"{campaign.label}\n{campaign.path.name}")
            item.setData(_DATA_ROLE, str(campaign.path))
            self._local_list.addItem(item)
        if self._local_list.count():
            self._local_list.setCurrentRow(0)

    def _new_campaign(self) -> None:
        name, ok = QInputDialog.getText(
            self, "New campaign", "What is it called?", text="Our campaign"
        )
        if not ok or not name.strip():
            return
        campaign = campaigns.create_local(name.strip())
        self._reload_local()
        for row in range(self._local_list.count()):
            if self._local_list.item(row).data(_DATA_ROLE) == str(campaign.path):
                self._local_list.setCurrentRow(row)
                break
        self._tabs.setCurrentIndex(LOCAL_TAB)

    def _delete_campaign(self) -> None:
        item = self._local_list.currentItem()
        if item is None:
            return
        path = Path(item.data(_DATA_ROLE))
        confirm = QMessageBox.question(
            self,
            "Delete campaign",
            f"Delete {path.name} for good?\n\n"
            "Everything in it goes: characters, places, transcripts and logins.",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            campaigns.delete_local(path)
            self._reload_local()

    # ---------------------------------------------------------------- online

    def _build_online_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Sessions on this network, and ones you have joined:"))

        self._remote_list = QListWidget()
        self._remote_list.itemSelectionChanged.connect(self._use_selected_remote)
        layout.addWidget(self._remote_list, 1)

        form = QFormLayout()
        self._url = QLineEdit()
        self._url.setPlaceholderText("ws://192.168.1.10:8765")
        form.addRow("Address", self._url)

        self._username = QLineEdit()
        form.addRow("Username", self._username)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.returnPressed.connect(self._accept)
        form.addRow("Password", self._password)
        layout.addLayout(form)

        self._remember = QCheckBox("Remember my password for this session")
        self._remember.setEnabled(credentials.is_available())
        if not credentials.is_available():
            self._remember.setToolTip(
                "This machine has no credential store, so passwords cannot be saved."
            )
        layout.addWidget(self._remember)

        self._autostart_remote = QCheckBox("Open this automatically next time")
        self._autostart_remote.toggled.connect(
            lambda on: on and self._remember.setChecked(True)
        )
        layout.addWidget(self._autostart_remote)

        note = QLabel(
            "Your password is never sent over the network. If saved, it goes to "
            "this computer's credential store, never to a file."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self._listener = discovery.Listener(self)
        self._listener.sessions_changed.connect(lambda _s: self._reload_remote())
        self._listener.start()
        return page

    def _reload_remote(self) -> None:
        selected = self._remote_list.currentItem()
        selected_url = selected.data(_DATA_ROLE) if selected is not None else None

        self._remote_list.clear()
        seen: set[str] = set()

        for session in self._listener.sessions:
            item = QListWidgetItem(f"{session.name}\non this network - {session.host}")
            item.setData(_DATA_ROLE, session.url)
            self._remote_list.addItem(item)
            seen.add(session.url)

        for remembered in campaigns.list_remote():
            if remembered.url in seen:
                continue
            item = QListWidgetItem(f"{remembered.label}\n{remembered.url}")
            item.setData(_DATA_ROLE, remembered.url)
            self._remote_list.addItem(item)

        for row in range(self._remote_list.count()):
            if self._remote_list.item(row).data(_DATA_ROLE) == selected_url:
                self._remote_list.setCurrentRow(row)
                break

    def _use_selected_remote(self) -> None:
        item = self._remote_list.currentItem()
        if item is None:
            return
        url = item.data(_DATA_ROLE)
        self._url.setText(url)
        for remembered in campaigns.list_remote():
            if remembered.url == url and remembered.username:
                self._username.setText(remembered.username)
                saved = credentials.load(url, remembered.username)
                if saved:
                    self._password.setText(saved)
                    self._remember.setChecked(True)
                else:
                    self._password.setFocus()
                break

        entry = campaigns.get_autostart()
        self._autostart_remote.setChecked(
            entry is not None and entry.kind == "remote" and entry.url == url
        )

    # ---------------------------------------------------------------- result

    def _update_buttons(self) -> None:
        open_button = self._buttons.button(QDialogButtonBox.StandardButton.Open)
        online = self._tabs.currentIndex() == ONLINE_TAB
        open_button.setText("Join" if online else "Open")

    def _accept(self) -> None:
        if self._tabs.currentIndex() == ONLINE_TAB:
            url = self._url.text().strip()
            username = self._username.text().strip()
            password = self._password.text()
            if not url or not username or not password:
                QMessageBox.information(
                    self,
                    "Join a session",
                    "An address, a username and a password are all needed.",
                )
                return
            item = self._remote_list.currentItem()
            name = item.text().split("\n")[0] if item is not None else ""
            self._launch = Launch(
                kind="remote",
                url=url,
                username=username,
                password=password,
                name=name,
                remember=self._remember.isChecked(),
                autostart=self._autostart_remote.isChecked(),
            )
        else:
            item = self._local_list.currentItem()
            if item is None:
                QMessageBox.information(
                    self,
                    "Open a campaign",
                    "Pick a campaign, or make a new one.",
                )
                return
            path = Path(item.data(_DATA_ROLE))
            self._launch = Launch(
                kind="local",
                path=path,
                name=item.text().split("\n")[0],
                autostart=self._autostart_local.isChecked(),
            )
        self.accept()

    def launch(self) -> Launch | None:
        return self._launch

    def done(self, result: int) -> None:  # noqa: D102 - Qt naming
        self._listener.stop()
        super().done(result)


def choose_campaign(start_online: bool = False) -> Launch | None:
    """Show the chooser. Returns None if the user backed out."""
    dialog = CampaignDialog(start_online=start_online)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.launch()
