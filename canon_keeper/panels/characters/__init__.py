"""Characters panel: NPCs and player characters."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from canon_keeper.plugin import API_VERSION, AppContext
from canon_keeper.panels.characters.player_widget import PlayerCharactersWidget
from canon_keeper.panels.characters.widget import CharactersWidget


class CharactersPanel:
    """Registered as ``canonkeeper.panels`` entry point ``characters``."""

    id = "characters"
    title = "Characters"
    api_version = API_VERSION
    roles = ("dm", "player")

    def create_widget(self, ctx: AppContext) -> QWidget:
        # Two widgets rather than one with fields hidden: a player's view is
        # built from data the host already filtered, so it has no secret to
        # leak even if the UI is wrong.
        if ctx.role == "player":
            return PlayerCharactersWidget(ctx)
        return CharactersWidget(ctx)

    def default_area(self) -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.LeftDockWidgetArea


__all__ = ["CharactersPanel", "CharactersWidget", "PlayerCharactersWidget"]
