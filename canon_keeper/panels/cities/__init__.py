"""Cities panel: the place hierarchy and who is standing in it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from canon_keeper.plugin import API_VERSION, AppContext
from canon_keeper.panels.cities.widget import CitiesWidget


class CitiesPanel:
    """Registered as ``canonkeeper.panels`` entry point ``cities``."""

    id = "cities"
    title = "Cities & Places"
    api_version = API_VERSION

    def create_widget(self, ctx: AppContext) -> QWidget:
        return CitiesWidget(ctx)

    def default_area(self) -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.RightDockWidgetArea


__all__ = ["CitiesPanel", "CitiesWidget"]
