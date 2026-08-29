"""The public plugin contract.

This module is the entire supported surface for third-party panels. Anything
not reachable from here is private and may change without warning.

A plugin package declares itself in its own ``pyproject.toml``::

    [project.entry-points."canonkeeper.panels"]
    weather = "my_pkg.panel:WeatherPanel"

and provides a class satisfying :class:`PanelPlugin`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from canon_keeper.bus import Bus
    from canon_keeper.repo import Repos

#: Bumped only on a breaking change to :class:`AppContext` or
#: :class:`PanelPlugin`. Panels declaring a different major version are skipped
#: rather than loaded, so an outdated plugin degrades to "absent", not "crash".
API_VERSION = 1

#: The entry-point group scanned at startup.
ENTRY_POINT_GROUP = "canonkeeper.panels"


class AppContext:
    """Everything a panel is given. Passed to :meth:`PanelPlugin.create_widget`."""

    def __init__(
        self,
        repos: "Repos",
        bus: "Bus",
        log: logging.Logger,
        campaign_id: int,
        api_version: int = API_VERSION,
        role: str = "dm",
        shared=None,
        names=None,
    ) -> None:
        self.repos = repos
        self.bus = bus
        self.log = log
        self.api_version = api_version
        #: "dm" or "player". Players run the same app with a reduced panel set;
        #: see PanelPlugin.roles.
        self.role = role
        #: In player mode, the host's filtered view of the campaign. Panels read
        #: this instead of `repos`, because a player's app is only ever shown
        #: what the host decided to send. None for the DM, who reads the real
        #: database.
        self.shared = shared
        #: Set when the app was launched by joining a session: the Table panel
        #: connects with it instead of making the player log in twice.
        self.pending_join: tuple[str, str, str] | None = None
        #: Resolves what each panel is called. A panel's `title` is only
        #: its default: the user or the DM may have renamed it.
        self.names = names
        #: Mutated by the shell when the DM opens a different campaign; the
        #: change is announced on ``bus.campaign_changed``.
        self.campaign_id = campaign_id


@runtime_checkable
class PanelPlugin(Protocol):
    """What an entry point must resolve to.

    The class is instantiated with no arguments, so keep ``__init__`` trivial and
    do real work in :meth:`create_widget`, where failures are contained.
    """

    #: Stable, unique, and used verbatim as the QDockWidget objectName. Changing
    #: it orphans every saved layout that mentions the panel, so treat it as
    #: permanent once released.
    id: str

    #: Shown in the dock title bar and the Panels menu.
    title: str

    #: The API_VERSION this panel was written against.
    api_version: int

    #: Optional. Which roles the panel appears for -- ("dm",), ("player",) or
    #: both. A panel that does not declare it is shown in every role, so
    #: existing plugins are unaffected.
    roles: tuple[str, ...]

    def create_widget(self, ctx: AppContext) -> QWidget:
        """Build the panel's contents. Raising here disables only this panel."""
        ...

    def default_area(self) -> Qt.DockWidgetArea:
        """Where the dock lands the first time, before any saved layout exists."""
        ...


class BasePanel:
    """Optional convenience base. Implementing the Protocol directly is fine."""

    id: str = "unnamed"
    title: str = "Unnamed"
    api_version: int = API_VERSION
    roles: tuple[str, ...] = ("dm", "player")

    def create_widget(self, ctx: AppContext) -> QWidget:  # pragma: no cover
        raise NotImplementedError

    def default_area(self) -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.LeftDockWidgetArea
