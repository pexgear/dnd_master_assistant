"""Light and dark appearance."""

from __future__ import annotations

import logging

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from canon_keeper.shell import theme as theme_module
from canon_keeper.shell.loader import LoadedPanel
from canon_keeper.shell.main_window import MainWindow
from canon_keeper.shell.theme import SETTING_KEY, Theme, ThemeController, build_palette

log = logging.getLogger("canonkeeper.test")


@pytest.fixture(autouse=True)
def _restore_palette(qapp):
    """Theme changes are application-wide, so undo them between tests."""
    original = qapp.palette()
    yield
    qapp.setPalette(original)
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Unknown)


# ------------------------------------------------------------------- palettes


def test_dark_palette_is_actually_dark():
    dark = build_palette(True)
    assert dark.window().color().lightness() < 128
    assert dark.base().color().lightness() < 128
    assert dark.text().color().lightness() > 128


def test_light_palette_is_actually_light():
    light = build_palette(False)
    assert light.window().color().lightness() > 128
    assert light.base().color().lightness() > 128
    assert light.text().color().lightness() < 128


@pytest.mark.parametrize("dark", [True, False])
def test_text_is_readable_against_its_background(dark):
    """A palette whose text vanishes into the page is the whole failure mode."""
    palette = build_palette(dark)
    pairs = [
        (palette.text().color(), palette.base().color()),
        (palette.windowText().color(), palette.window().color()),
        (palette.buttonText().color(), palette.button().color()),
        (palette.highlightedText().color(), palette.highlight().color()),
    ]
    for foreground, background in pairs:
        assert abs(foreground.lightness() - background.lightness()) > 60


@pytest.mark.parametrize("dark", [True, False])
def test_disabled_text_is_muted_but_not_invisible(dark):
    palette = build_palette(dark)
    normal = palette.color(QPalette.ColorGroup.Normal, QPalette.ColorRole.Text)
    disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    background = palette.base().color()

    assert disabled != normal
    assert abs(disabled.lightness() - background.lightness()) > 25


# ----------------------------------------------------------------- resolution


def test_explicit_choices_ignore_the_system(monkeypatch):
    monkeypatch.setattr(theme_module, "system_prefers_dark", lambda: True)
    assert theme_module.resolve(Theme.LIGHT) is False
    assert theme_module.resolve(Theme.DARK) is True


def test_system_choice_follows_the_system(monkeypatch):
    monkeypatch.setattr(theme_module, "system_prefers_dark", lambda: True)
    assert theme_module.resolve(Theme.SYSTEM) is True
    monkeypatch.setattr(theme_module, "system_prefers_dark", lambda: False)
    assert theme_module.resolve(Theme.SYSTEM) is False


# ----------------------------------------------------------------- controller


def test_preference_is_persisted_and_reloaded(ctx, qapp):
    controller = ThemeController(qapp, ctx.repos.settings)
    controller.set_theme(Theme.DARK)

    assert ctx.repos.settings.get(SETTING_KEY) == "dark"
    assert ThemeController(qapp, ctx.repos.settings).theme is Theme.DARK


def test_unknown_stored_value_falls_back_to_system(ctx, qapp):
    ctx.repos.settings.set(SETTING_KEY, "chartreuse")
    assert ThemeController(qapp, ctx.repos.settings).theme is Theme.SYSTEM


def test_applying_a_theme_changes_the_application_palette(ctx, qapp):
    controller = ThemeController(qapp, ctx.repos.settings)

    controller.set_theme(Theme.DARK)
    assert qapp.palette().base().color().lightness() < 128

    controller.set_theme(Theme.LIGHT)
    assert qapp.palette().base().color().lightness() > 128


def test_theme_change_is_announced(ctx, qapp, qtbot):
    controller = ThemeController(qapp, ctx.repos.settings)
    controller.changed.connect(ctx.bus.theme_changed)

    with qtbot.waitSignal(ctx.bus.theme_changed, timeout=1000) as blocker:
        controller.set_theme(Theme.DARK)
    assert blocker.args == [True]

    with qtbot.waitSignal(ctx.bus.theme_changed, timeout=1000) as blocker:
        controller.set_theme(Theme.LIGHT)
    assert blocker.args == [False]


def test_system_scheme_change_only_acts_while_following(ctx, qapp, monkeypatch):
    controller = ThemeController(qapp, ctx.repos.settings)
    controller.set_theme(Theme.LIGHT)

    monkeypatch.setattr(theme_module, "system_prefers_dark", lambda: True)
    controller._on_system_scheme_changed(Qt.ColorScheme.Dark)
    assert controller.is_dark is False, "an explicit choice must not be overridden"

    controller.set_theme(Theme.SYSTEM)
    assert controller.is_dark is True


# --------------------------------------------------------------------- window


class _Panel:
    id = "alpha"
    title = "Alpha"
    api_version = 1

    def create_widget(self, ctx):
        from PySide6.QtWidgets import QLabel

        return QLabel("alpha")

    def default_area(self):
        return Qt.DockWidgetArea.LeftDockWidgetArea


def test_view_menu_offers_every_theme(ctx, qtbot, qapp):
    controller = ThemeController(qapp, ctx.repos.settings)
    window = MainWindow(
        ctx, [LoadedPanel(_Panel(), "alpha", "tests:Alpha")], [], log, controller
    )
    qtbot.addWidget(window)

    labels = {action.text() for action in window._theme_group.actions()}
    assert labels == {t.label for t in Theme}
    assert sum(a.isChecked() for a in window._theme_group.actions()) == 1


def test_choosing_a_theme_from_the_menu_applies_it(ctx, qtbot, qapp):
    controller = ThemeController(qapp, ctx.repos.settings)
    window = MainWindow(
        ctx, [LoadedPanel(_Panel(), "alpha", "tests:Alpha")], [], log, controller
    )
    qtbot.addWidget(window)

    dark_action = next(
        a for a in window._theme_group.actions() if a.data() == Theme.DARK.value
    )
    dark_action.trigger()

    assert controller.theme is Theme.DARK
    assert qapp.palette().base().color().lightness() < 128


def test_window_still_builds_without_a_theme_controller(ctx, qtbot):
    """The controller is optional, so a panel test need not construct one."""
    window = MainWindow(ctx, [LoadedPanel(_Panel(), "alpha", "tests:Alpha")], [], log)
    qtbot.addWidget(window)
    assert all(not a.isEnabled() for a in window._theme_group.actions())
