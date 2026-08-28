"""Table panel: the shared chat and dice channel for a session."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from canon_keeper.plugin import API_VERSION, AppContext
from canon_keeper.panels.table.widget import TableWidget


class TablePanel:
    """Registered as ``canonkeeper.panels`` entry point ``table``."""

    id = "table"
    title = "Table"
    api_version = API_VERSION
    roles = ("dm", "player")

    def create_widget(self, ctx: AppContext) -> QWidget:
        return TableWidget(ctx)

    def default_area(self) -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.RightDockWidgetArea


__all__ = ["TablePanel", "TableWidget"]
