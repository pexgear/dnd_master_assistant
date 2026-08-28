"""Cities panel: the place hierarchy and who is standing in it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from canon_keeper.plugin import API_VERSION, AppContext
from canon_keeper.panels.cities.player_widget import PlayerCitiesWidget
from canon_keeper.panels.cities.widget import CitiesWidget


class CitiesPanel:
    """Registered as ``canonkeeper.panels`` entry point ``cities``."""

    id = "cities"
    title = "Cities & Places"
    api_version = API_VERSION
    roles = ("dm", "player")

    def create_widget(self, ctx: AppContext) -> QWidget:
        # Two widgets rather than one with fields hidden: a player's view is
        # built from data the host already filtered, so it has no secret to
        # leak even if the UI is wrong.
        if ctx.role == "player":
            return PlayerCitiesWidget(ctx)
        return CitiesWidget(ctx)

    def default_area(self) -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.RightDockWidgetArea


__all__ = ["CitiesPanel", "CitiesWidget", "PlayerCitiesWidget"]
