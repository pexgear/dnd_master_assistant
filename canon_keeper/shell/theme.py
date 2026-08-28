"""Light and dark appearance.

Qt 6 can derive a palette from the platform's colour scheme, but only where the
platform theme implements it -- it is a no-op under the offscreen plugin, and it
varies by desktop on Linux. So the palettes here are explicit: the same two
appearances on all three platforms, and testable headlessly.

The colour-scheme hint is set as well as the palette. The palette dresses the
widgets; the hint is what tells Windows 11 to draw a dark title bar, so without
it a dark app keeps a white frame.
"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette

SETTING_KEY = "theme"


class Theme(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @property
    def label(self) -> str:
        return {"system": "Follow System", "light": "Light", "dark": "Dark"}[self.value]


_LIGHT = {
    QPalette.ColorRole.Window: "#efefef",
    QPalette.ColorRole.WindowText: "#1a1a1a",
    QPalette.ColorRole.Base: "#ffffff",
    QPalette.ColorRole.AlternateBase: "#f6f6f6",
    QPalette.ColorRole.ToolTipBase: "#ffffdc",
    QPalette.ColorRole.ToolTipText: "#1a1a1a",
    QPalette.ColorRole.Text: "#1a1a1a",
    QPalette.ColorRole.Button: "#efefef",
    QPalette.ColorRole.ButtonText: "#1a1a1a",
    QPalette.ColorRole.BrightText: "#b00020",
    QPalette.ColorRole.Link: "#1a5fb4",
    QPalette.ColorRole.Highlight: "#3584e4",
    QPalette.ColorRole.HighlightedText: "#ffffff",
    QPalette.ColorRole.PlaceholderText: "#767676",
}

_DARK = {
    QPalette.ColorRole.Window: "#2b2b2b",
    QPalette.ColorRole.WindowText: "#e6e6e6",
    QPalette.ColorRole.Base: "#1e1e1e",
    QPalette.ColorRole.AlternateBase: "#2b2b2b",
    QPalette.ColorRole.ToolTipBase: "#2b2b2b",
    QPalette.ColorRole.ToolTipText: "#e6e6e6",
    QPalette.ColorRole.Text: "#e6e6e6",
    QPalette.ColorRole.Button: "#323232",
    QPalette.ColorRole.ButtonText: "#e6e6e6",
    QPalette.ColorRole.BrightText: "#ff6b6b",
    QPalette.ColorRole.Link: "#7aa2f7",
    QPalette.ColorRole.Highlight: "#3584e4",
    QPalette.ColorRole.HighlightedText: "#ffffff",
    QPalette.ColorRole.PlaceholderText: "#8b8b8b",
}

_DISABLED_TEXT = {"light": "#a4a4a4", "dark": "#6b6b6b"}


def build_palette(dark: bool) -> QPalette:
    palette = QPalette()
    for role, colour in (_DARK if dark else _LIGHT).items():
        palette.setColor(role, QColor(colour))

    # Without these, disabled controls keep the enabled text colour and a greyed
    # button is indistinguishable from a live one.
    muted = QColor(_DISABLED_TEXT["dark" if dark else "light"])
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.HighlightedText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Highlight,
        QColor("#4a4a4a" if dark else "#c9c9c9"),
    )
    return palette


def system_prefers_dark() -> bool:
    """What the OS is asking for, defaulting to light when it will not say."""
    hints = QGuiApplication.styleHints()
    if hints is None or not hasattr(hints, "colorScheme"):  # pragma: no cover
        return False
    return hints.colorScheme() == Qt.ColorScheme.Dark


def resolve(theme: Theme) -> bool:
    """Turn a preference into the concrete question: dark, yes or no?"""
    if theme is Theme.DARK:
        return True
    if theme is Theme.LIGHT:
        return False
    return system_prefers_dark()


class ThemeController(QObject):
    """Owns the appearance preference and keeps the application in step."""

    changed = Signal(bool)  # is_dark

    def __init__(self, app, settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._settings = settings
        self._theme = self._load()
        self._is_dark = resolve(self._theme)

        hints = QGuiApplication.styleHints()
        if hints is not None and hasattr(hints, "colorSchemeChanged"):
            # Only acted on while following the system, but stay connected so
            # switching back to "Follow System" picks up the current setting.
            hints.colorSchemeChanged.connect(self._on_system_scheme_changed)

    def _load(self) -> Theme:
        raw = self._settings.get(SETTING_KEY, Theme.SYSTEM.value)
        try:
            return Theme(raw)
        except ValueError:
            return Theme.SYSTEM

    @property
    def theme(self) -> Theme:
        return self._theme

    @property
    def is_dark(self) -> bool:
        return self._is_dark

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._settings.set(SETTING_KEY, theme.value)
        self.apply()

    def apply(self) -> None:
        dark = resolve(self._theme)
        self._is_dark = dark

        hints = QGuiApplication.styleHints()
        if hints is not None and hasattr(hints, "setColorScheme"):
            # Unknown means "stop overriding and follow the platform".
            hints.setColorScheme(
                Qt.ColorScheme.Unknown
                if self._theme is Theme.SYSTEM
                else (Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
            )

        self._app.setPalette(build_palette(dark))
        self.changed.emit(dark)

    def _on_system_scheme_changed(self, _scheme) -> None:
        if self._theme is Theme.SYSTEM:
            self.apply()
