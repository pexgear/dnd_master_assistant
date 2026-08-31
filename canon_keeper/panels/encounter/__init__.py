"""Combat panel: the initiative order and the grid everyone is standing on."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from canon_keeper.panels.encounter.player_widget import PlayerEncounterWidget
from canon_keeper.panels.encounter.widget import EncounterWidget
from canon_keeper.plugin import API_VERSION, AppContext


class EncounterPanel:
    """Registered as ``canonkeeper.panels`` entry point ``encounter``."""

    id = "encounter"
    title = "Combat"
    api_version = API_VERSION
    roles = ("dm", "player")

    def create_widget(self, ctx: AppContext) -> QWidget:
        # Two widgets rather than one with the buttons hidden. A player's map is
        # drawn from what the host sent, so there is nothing on it to leak even
        # if this class is wrong about which one to build.
        if ctx.role == "player":
            return PlayerEncounterWidget(ctx)
        return EncounterWidget(ctx)

    def default_area(self) -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.BottomDockWidgetArea


__all__ = ["EncounterPanel", "EncounterWidget", "PlayerEncounterWidget"]
