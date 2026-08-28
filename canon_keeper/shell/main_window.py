"""The dockable workspace.

Every panel lives in a QDockWidget, so undocking it produces a real OS window
you can drop on a second monitor, and the whole arrangement round-trips through
``saveState``/``restoreState``.

One rule matters more than the rest: **every dock must have an objectName** --
we use the panel id -- because Qt silently drops nameless docks when restoring,
and the symptom looks like "my layout keeps resetting itself".
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QWidget,
)

from canon_keeper import __version__, config
from canon_keeper.plugin import API_VERSION, AppContext
from canon_keeper.repo.layouts import AUTOSAVE_NAME
from canon_keeper.shell.loader import LoadedPanel, LoadError

#: Bumped if the set of docks changes in a way that makes old saved states
#: meaningless. Qt refuses to restore a state saved under a different version,
#: which is the desired behaviour -- a stale layout is dropped, not misapplied.
LAYOUT_VERSION = 1


class MainWindow(QMainWindow):
    def __init__(
        self,
        ctx: AppContext,
        panels: list[LoadedPanel],
        errors: list[LoadError],
        log: logging.Logger,
    ) -> None:
        super().__init__()
        self._ctx = ctx
        self._log = log
        self._errors = list(errors)
        self._docks: dict[str, QDockWidget] = {}
        self._panels: dict[str, LoadedPanel] = {}

        self.setObjectName("CanonKeeperMainWindow")
        self.resize(1280, 820)
        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AnimatedDocks
            | QMainWindow.DockOption.GroupedDragging
        )

        # A zero-size central widget lets the docks occupy the entire window.
        # QMainWindow reserves the centre for a central widget whether or not one
        # is useful here, and this is the standard way to give that space back.
        filler = QWidget(self)
        filler.setMaximumSize(0, 0)
        self.setCentralWidget(filler)

        self._build_panels(panels)
        self._build_menus()

        self._ctx.bus.status_message.connect(self._on_status_message)
        self._ctx.bus.campaign_changed.connect(lambda _id: self._update_title())

        self._update_title()
        self._restore_initial_layout()

        if self._errors:
            self.statusBar().showMessage(
                f"{len(self._errors)} panel(s) failed to load - see Help > Installed Panels",
                8000,
            )

    # ------------------------------------------------------------------ panels

    def _build_panels(self, panels: list[LoadedPanel]) -> None:
        for entry in panels:
            plugin = entry.plugin
            try:
                widget = plugin.create_widget(self._ctx)
                area = plugin.default_area()
            except Exception as exc:  # noqa: BLE001 - contain the blast radius
                self._log.exception("panel %r failed to build", plugin.id)
                self._errors.append(
                    LoadError(entry.entry_point, f"create_widget() raised: {exc}")
                )
                continue

            dock = QDockWidget(plugin.title, self)
            # Non-negotiable: without this, restoreState() drops the dock.
            dock.setObjectName(plugin.id)
            dock.setWidget(widget)
            dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
            dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                | QDockWidget.DockWidgetFeature.DockWidgetClosable
            )
            self.addDockWidget(area, dock)
            self._docks[plugin.id] = dock
            self._panels[plugin.id] = entry

    # ------------------------------------------------------------------- menus

    def _build_menus(self) -> None:
        bar = self.menuBar()

        # --- File -----------------------------------------------------------
        file_menu = bar.addMenu("&File")
        self._campaign_menu = file_menu.addMenu("&Campaign")
        self._campaign_menu.aboutToShow.connect(self._populate_campaign_menu)

        act_new = QAction("&New Campaign...", self)
        act_new.triggered.connect(self._new_campaign)
        file_menu.addAction(act_new)

        act_rename = QAction("&Rename Campaign...", self)
        act_rename.triggered.connect(self._rename_campaign)
        file_menu.addAction(act_rename)

        file_menu.addSeparator()
        act_folder = QAction("Open &Data Folder", self)
        act_folder.triggered.connect(self._open_data_folder)
        file_menu.addAction(act_folder)

        file_menu.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # --- Panels ---------------------------------------------------------
        panels_menu = bar.addMenu("&Panels")
        for panel_id, dock in self._docks.items():
            action = dock.toggleViewAction()
            action.setObjectName(f"toggle_{panel_id}")
            panels_menu.addAction(action)
        panels_menu.addSeparator()
        act_show_all = QAction("Show &All Panels", self)
        act_show_all.triggered.connect(self._show_all_panels)
        panels_menu.addAction(act_show_all)

        # --- Layouts --------------------------------------------------------
        self._layouts_menu = bar.addMenu("&Layouts")
        self._layouts_menu.aboutToShow.connect(self._populate_layouts_menu)
        self._populate_layouts_menu()

        # --- Help -----------------------------------------------------------
        help_menu = bar.addMenu("&Help")
        act_plugins = QAction("Installed &Panels...", self)
        act_plugins.triggered.connect(self._show_plugins_dialog)
        help_menu.addAction(act_plugins)
        act_about = QAction("&About", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _open_data_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.data_dir())))

    def _populate_campaign_menu(self) -> None:
        self._campaign_menu.clear()
        for campaign in self._ctx.repos.campaigns.list():
            action = QAction(campaign.name, self)
            action.setCheckable(True)
            action.setChecked(campaign.id == self._ctx.campaign_id)
            action.triggered.connect(
                lambda _checked=False, cid=campaign.id: self._switch_campaign(cid)
            )
            self._campaign_menu.addAction(action)

    def _populate_layouts_menu(self) -> None:
        self._layouts_menu.clear()

        act_save = QAction("&Save Current Layout...", self)
        act_save.triggered.connect(self._save_layout_as)
        self._layouts_menu.addAction(act_save)

        act_reset = QAction("&Reset to Default Arrangement", self)
        act_reset.triggered.connect(self._apply_default_arrangement)
        self._layouts_menu.addAction(act_reset)

        layouts = self._ctx.repos.layouts.list()
        if layouts:
            self._layouts_menu.addSeparator()
            for layout in layouts:
                label = layout.name + (" *" if layout.is_default else "")
                sub = self._layouts_menu.addMenu(label)

                act_apply = QAction("Apply", self)
                act_apply.triggered.connect(
                    lambda _c=False, n=layout.name: self._apply_layout(n)
                )
                sub.addAction(act_apply)

                act_overwrite = QAction("Overwrite with current", self)
                act_overwrite.triggered.connect(
                    lambda _c=False, n=layout.name: self._save_layout(n)
                )
                sub.addAction(act_overwrite)

                act_default = QAction("Open on startup", self)
                act_default.setCheckable(True)
                act_default.setChecked(layout.is_default)
                act_default.triggered.connect(
                    lambda _c=False, n=layout.name: self._ctx.repos.layouts.set_default(n)
                )
                sub.addAction(act_default)

                sub.addSeparator()
                act_delete = QAction("Delete", self)
                act_delete.triggered.connect(
                    lambda _c=False, n=layout.name: self._delete_layout(n)
                )
                sub.addAction(act_delete)

    # ----------------------------------------------------------------- layouts

    def _save_layout(self, name: str, *, is_default: bool = False) -> None:
        self._ctx.repos.layouts.save(
            name,
            bytes(self.saveGeometry()),
            bytes(self.saveState(LAYOUT_VERSION)),
            is_default=is_default,
        )
        self._log.info("saved layout %r", name)

    def _save_layout_as(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Save Layout", "Layout name:", text="At the table"
        )
        name = name.strip()
        if not ok or not name:
            return
        if name == AUTOSAVE_NAME:
            QMessageBox.warning(self, "Save Layout", f"{AUTOSAVE_NAME} is a reserved name.")
            return
        self._save_layout(name)
        self.statusBar().showMessage(f"Layout {name} saved", 4000)

    def _apply_layout(self, name: str) -> bool:
        layout = self._ctx.repos.layouts.get(name)
        if layout is None:
            return False
        self.restoreGeometry(QByteArray(layout.geometry))
        # Docks named in the state whose panel is not installed are skipped by
        # Qt; the rest of the arrangement still restores.
        ok = self.restoreState(QByteArray(layout.state), LAYOUT_VERSION)
        if not ok:
            self._log.warning("layout %r could not be restored (version mismatch?)", name)
        return ok

    def _delete_layout(self, name: str) -> None:
        confirm = QMessageBox.question(self, "Delete Layout", f"Delete the layout {name}?")
        if confirm == QMessageBox.StandardButton.Yes:
            self._ctx.repos.layouts.delete(name)
            self.statusBar().showMessage(f"Layout {name} deleted", 4000)

    def _restore_initial_layout(self) -> None:
        default = self._ctx.repos.layouts.default()
        if default and self._apply_layout(default.name):
            self._log.info("restored default layout %r", default.name)
            return
        if self._apply_layout(AUTOSAVE_NAME):
            self._log.info("restored previous session layout")
            return
        self._log.info("no saved layout; using default arrangement")

    def _apply_default_arrangement(self) -> None:
        """Put every dock back where its plugin asked to be, and show it."""
        for panel_id, dock in self._docks.items():
            entry = self._panels[panel_id]
            dock.setFloating(False)
            self.removeDockWidget(dock)
            try:
                area = entry.plugin.default_area()
            except Exception:  # noqa: BLE001
                area = Qt.DockWidgetArea.LeftDockWidgetArea
            self.addDockWidget(area, dock)
            dock.show()
        self.statusBar().showMessage("Panels reset to their default arrangement", 4000)

    def _show_all_panels(self) -> None:
        for dock in self._docks.values():
            dock.show()

    # --------------------------------------------------------------- campaigns

    def _switch_campaign(self, campaign_id: int) -> None:
        if campaign_id == self._ctx.campaign_id:
            return
        self._ctx.campaign_id = campaign_id
        self._ctx.bus.campaign_changed.emit(campaign_id)
        self._update_title()

    def _new_campaign(self) -> None:
        name, ok = QInputDialog.getText(self, "New Campaign", "Campaign name:")
        name = name.strip()
        if not ok or not name:
            return
        campaign = self._ctx.repos.campaigns.create(name)
        self._switch_campaign(campaign.id)

    def _rename_campaign(self) -> None:
        current = self._ctx.repos.campaigns.get(self._ctx.campaign_id)
        if current is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename Campaign", "Campaign name:", text=current.name
        )
        name = name.strip()
        if ok and name:
            self._ctx.repos.campaigns.rename(current.id, name)
            self._update_title()

    def _update_title(self) -> None:
        campaign = self._ctx.repos.campaigns.get(self._ctx.campaign_id)
        label = campaign.name if campaign else "no campaign"
        self.setWindowTitle(f"Canon Keeper - {label}")

    # -------------------------------------------------------------------- misc

    def _on_status_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 5000)

    def _show_plugins_dialog(self) -> None:
        lines = [f"Panel API version {API_VERSION}", ""]
        if self._panels:
            lines.append("Loaded:")
            lines += [
                f"  - {e.plugin.title}  [{e.plugin.id}]  from {e.module}"
                for e in self._panels.values()
            ]
        else:
            lines.append("No panels loaded.")
        if self._errors:
            lines += ["", "Failed:"]
            lines += [f"  - {err.entry_point}: {err.reason}" for err in self._errors]
        QMessageBox.information(self, "Installed Panels", "\n".join(lines))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Canon Keeper",
            f"<b>Canon Keeper</b> {__version__}<br><br>"
            "A dockable desktop assistant for running D&amp;D 5e.<br>"
            "What the DM actually says is the only source of truth.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        try:
            self._save_layout(AUTOSAVE_NAME)
        except Exception:  # noqa: BLE001 - never block exit on a bad write
            self._log.exception("failed to autosave layout")
        super().closeEvent(event)
