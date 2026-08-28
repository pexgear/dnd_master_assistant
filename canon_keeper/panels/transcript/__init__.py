"""Transcript panel: record what you say, see it on screen."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from canon_keeper.plugin import API_VERSION, AppContext
from canon_keeper.panels.transcript.widget import TranscriptWidget


class TranscriptPanel:
    """Registered as ``canonkeeper.panels`` entry point ``transcript``."""

    id = "transcript"
    title = "Transcript"
    api_version = API_VERSION

    def create_widget(self, ctx: AppContext) -> QWidget:
        return TranscriptWidget(ctx)

    def default_area(self) -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.BottomDockWidgetArea


__all__ = ["TranscriptPanel", "TranscriptWidget"]
