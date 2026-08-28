"""Panel discovery.

Everything visible in the window arrives through this loader, first-party panels
included. Dogfooding the entry-point path is the only way to be sure a
third-party plugin will work.

The contract with the user is that a bad plugin costs them that panel and
nothing else: a panel that fails to import, declares the wrong API version, or
raises while building its widget is disabled and reported, never fatal.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points

from canon_keeper.plugin import API_VERSION, ENTRY_POINT_GROUP, PanelPlugin


@dataclass(slots=True)
class LoadedPanel:
    plugin: PanelPlugin
    entry_point: str
    module: str


@dataclass(slots=True)
class LoadError:
    entry_point: str
    reason: str


def _disabled_ids() -> set[str]:
    """Panel ids listed in ``CANONKEEPER_DISABLE_PLUGINS`` (comma separated).

    An escape hatch for the case where a third-party panel crashes the app hard
    enough that the Plugins dialog is unreachable.
    """
    raw = os.environ.get("CANONKEEPER_DISABLE_PLUGINS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _validate(plugin: object, name: str) -> str | None:
    """Return a human-readable reason to reject the plugin, or None to accept."""
    for attr in ("id", "title", "api_version"):
        if not hasattr(plugin, attr):
            return f"missing required attribute '{attr}'"
    if not isinstance(plugin.id, str) or not plugin.id:
        return "'id' must be a non-empty string"
    if not callable(getattr(plugin, "create_widget", None)):
        return "missing create_widget()"
    if not callable(getattr(plugin, "default_area", None)):
        return "missing default_area()"
    if plugin.api_version != API_VERSION:
        return (
            f"built against API version {plugin.api_version}, "
            f"this build speaks version {API_VERSION}"
        )
    return None


#: First-party panels, as ``entry point name -> import path``. These are declared
#: in pyproject.toml and normally arrive through the entry-point scan like any
#: third-party panel. This list is only consulted when the scan finds nothing,
#: which is what happens when the app is run straight from a source checkout
#: that has not been pip-installed -- pressing Run in an editor, typically.
BUILTIN_PANELS = {
    "characters": "canon_keeper.panels.characters:CharactersPanel",
    "cities": "canon_keeper.panels.cities:CitiesPanel",
    "transcript": "canon_keeper.panels.transcript:TranscriptPanel",
    "table": "canon_keeper.panels.table:TablePanel",
}


def _builtin_entry_points() -> list[EntryPoint]:
    return [
        EntryPoint(name=name, value=value, group=ENTRY_POINT_GROUP)
        for name, value in sorted(BUILTIN_PANELS.items())
    ]


def discover_panels(
    log: logging.Logger | None = None,
    group: str = ENTRY_POINT_GROUP,
    role: str | None = None,
) -> tuple[list[LoadedPanel], list[LoadError]]:
    """Load every registered panel. Returns ``(loaded, errors)``.

    ``role`` filters to panels that declare they belong in it. A panel with no
    ``roles`` attribute is shown in every role.
    """
    log = log or logging.getLogger("canonkeeper.loader")
    loaded: list[LoadedPanel] = []
    errors: list[LoadError] = []
    seen_ids: set[str] = set()
    disabled = _disabled_ids()

    points: list[EntryPoint] = sorted(entry_points(group=group), key=lambda ep: ep.name)
    if not points and group == ENTRY_POINT_GROUP:
        log.info("no entry points registered; falling back to built-in panels "
                 "(run 'pip install -e .' to enable third-party plugins)")
        points = _builtin_entry_points()

    for point in points:
        try:
            factory = point.load()
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not be fatal
            log.exception("plugin %r failed to import", point.name)
            errors.append(LoadError(point.name, f"import failed: {exc}"))
            continue

        try:
            plugin = factory() if isinstance(factory, type) else factory
        except Exception as exc:  # noqa: BLE001
            log.exception("plugin %r failed to instantiate", point.name)
            errors.append(LoadError(point.name, f"construction failed: {exc}"))
            continue

        reason = _validate(plugin, point.name)
        if reason:
            log.warning("plugin %r rejected: %s", point.name, reason)
            errors.append(LoadError(point.name, reason))
            continue

        if plugin.id in disabled:
            log.info("plugin %r disabled via CANONKEEPER_DISABLE_PLUGINS", plugin.id)
            errors.append(LoadError(point.name, "disabled by CANONKEEPER_DISABLE_PLUGINS"))
            continue

        if plugin.id in seen_ids:
            # Two panels sharing an id would collide in saved layouts, because the
            # id is the dock's objectName.
            log.warning("plugin %r rejected: duplicate id %r", point.name, plugin.id)
            errors.append(LoadError(point.name, f"duplicate panel id {plugin.id!r}"))
            continue

        if role is not None:
            roles = getattr(plugin, "roles", None)
            if roles is not None and role not in roles:
                log.debug("panel %r is not shown in role %r", plugin.id, role)
                continue

        seen_ids.add(plugin.id)
        loaded.append(LoadedPanel(plugin=plugin, entry_point=point.name, module=point.value))
        log.info("loaded panel %r from %s", plugin.id, point.value)

    return loaded, errors
