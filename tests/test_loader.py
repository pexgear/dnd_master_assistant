"""The loader's contract: a bad plugin costs you that panel and nothing else."""

from __future__ import annotations

import logging
from importlib.metadata import EntryPoint

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from canon_keeper.plugin import API_VERSION
from canon_keeper.shell import loader

log = logging.getLogger("canonkeeper.test")


class GoodPanel:
    id = "good"
    title = "Good"
    api_version = API_VERSION

    def create_widget(self, ctx):
        return QLabel("fine")

    def default_area(self):
        return Qt.DockWidgetArea.LeftDockWidgetArea


class StalePanel(GoodPanel):
    id = "stale"
    api_version = API_VERSION + 1


class NamelessPanel:
    title = "No id"
    api_version = API_VERSION

    def create_widget(self, ctx):
        return QLabel("nope")

    def default_area(self):
        return Qt.DockWidgetArea.LeftDockWidgetArea


class ExplodingPanel(GoodPanel):
    id = "boom"

    def __init__(self):
        raise RuntimeError("kaboom")


def _patch_entry_points(monkeypatch, mapping: dict[str, object]) -> None:
    """Serve fake entry points that resolve to the given objects."""
    points = [
        EntryPoint(name=name, value=f"tests.fake:{name}", group=loader.ENTRY_POINT_GROUP)
        for name in mapping
    ]
    monkeypatch.setattr(loader, "entry_points", lambda group: points)
    monkeypatch.setattr(
        EntryPoint, "load", lambda self: mapping[self.name], raising=False
    )


def test_good_panel_loads(monkeypatch):
    _patch_entry_points(monkeypatch, {"good": GoodPanel})
    loaded, errors = loader.discover_panels(log)
    assert [entry.plugin.id for entry in loaded] == ["good"]
    assert errors == []


def test_wrong_api_version_is_skipped_not_fatal(monkeypatch):
    _patch_entry_points(monkeypatch, {"good": GoodPanel, "stale": StalePanel})
    loaded, errors = loader.discover_panels(log)
    assert [entry.plugin.id for entry in loaded] == ["good"]
    assert len(errors) == 1
    assert "API version" in errors[0].reason


def test_panel_missing_required_attribute_is_rejected(monkeypatch):
    _patch_entry_points(monkeypatch, {"nameless": NamelessPanel})
    loaded, errors = loader.discover_panels(log)
    assert loaded == []
    assert "id" in errors[0].reason


def test_panel_raising_in_constructor_does_not_stop_the_others(monkeypatch):
    _patch_entry_points(monkeypatch, {"boom": ExplodingPanel, "good": GoodPanel})
    loaded, errors = loader.discover_panels(log)
    assert [entry.plugin.id for entry in loaded] == ["good"]
    assert any("construction failed" in e.reason for e in errors)


def test_duplicate_ids_are_rejected(monkeypatch):
    class Clone(GoodPanel):
        pass

    _patch_entry_points(monkeypatch, {"good": GoodPanel, "zzz_clone": Clone})
    loaded, errors = loader.discover_panels(log)
    # Two docks sharing an objectName would collide in every saved layout.
    assert [entry.plugin.id for entry in loaded] == ["good"]
    assert any("duplicate panel id" in e.reason for e in errors)


def test_env_var_can_disable_a_panel(monkeypatch):
    _patch_entry_points(monkeypatch, {"good": GoodPanel})
    monkeypatch.setenv("CANONKEEPER_DISABLE_PLUGINS", "good")
    loaded, errors = loader.discover_panels(log)
    assert loaded == []
    assert "CANONKEEPER_DISABLE_PLUGINS" in errors[0].reason


def test_builtin_fallback_when_nothing_is_registered(monkeypatch):
    """Running from a source checkout with no install still gets its panels."""
    monkeypatch.setattr(loader, "entry_points", lambda group: [])
    loaded, errors = loader.discover_panels(log)
    assert {entry.plugin.id for entry in loaded} == {"characters", "cities"}
    assert errors == []


def test_first_party_panels_are_registered_as_entry_points():
    """The shipped panels must go through the same path third parties use."""
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group=loader.ENTRY_POINT_GROUP)}
    if not names:
        pytest.skip("package is not installed; entry points unavailable")
    assert {"characters", "cities"} <= names
