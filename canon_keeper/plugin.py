"""The public plugin contract.

This module is the entire supported surface for third-party panels. Anything
not reachable from here is private and may change without warning.

A plugin package declares itself in its own ``pyproject.toml``::

    [project.entry-points."canonkeeper.panels"]
    weather = "my_pkg.panel:WeatherPanel"

and provides a class satisfying :class:`PanelPlugin`.

A panel's **widget** may also implement ``panel_actions()``, returning a list of
:class:`PanelAction`. The shell gives any panel that does a menu of its own, so
what a panel can do is reachable without going to look at the panel first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from canon_keeper.bus import Bus
    from canon_keeper.repo import Repos

#: Bumped only on a breaking change to :class:`AppContext` or
#: :class:`PanelPlugin`. Panels declaring a different major version are skipped
#: rather than loaded, so an outdated plugin degrades to "absent", not "crash".
#:
#: 2 added :mod:`canon_keeper.entity_actions` -- the menu a creature carries
#: with it wherever it is shown -- and ``AppContext.session_address``.
API_VERSION = 2

#: The entry-point group scanned at startup.
ENTRY_POINT_GROUP = "canonkeeper.panels"


@dataclass(frozen=True)
class PanelAction:
    """One item in a panel's own menu.

    A panel's buttons live inside it, which is fine while you are looking at
    that panel and useless when you are not: starting a fight from the
    Characters panel meant finding Combat first. A widget may list the things
    it can do, and the shell gives it a menu of its own next to File and View.

    ``run`` is called with no arguments and is a bound method of the widget, so
    it has whatever state it needs. Keep ``label`` short -- it is a menu item,
    not a sentence.
    """

    label: str
    run: "Callable[[], None]"
    #: A key sequence, e.g. "Ctrl+Shift+N". Empty for none. The shell does not
    #: check for clashes: two panels claiming the same key is a thing their
    #: authors have to sort out between them, and Qt shows both.
    shortcut: str = ""
    #: Shown greyed when False. Evaluated when the menu is built, so a panel
    #: that wants this to change should say so on the bus and let the shell
    #: rebuild rather than holding a reference to the action.
    enabled: bool = True


@dataclass(frozen=True)
class PendingJoin:
    """A session the app was launched to join, carried to the Table panel.

    A record rather than a tuple because it grew a fourth thing -- an invite --
    and a four-tuple unpacked in one place and indexed in another is how the
    fourth thing quietly becomes the third.
    """

    url: str
    username: str
    password: str
    #: Set when the person is arriving on an invite rather than a login they
    #: already have. The Table panel enrols instead of logging in.
    invite: str = ""


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
        self.pending_join: PendingJoin | None = None
        #: Resolves what each panel is called. A panel's `title` is only
        #: its default: the user or the DM may have renamed it.
        self.names = names
        #: Mutated by the shell when the DM opens a different campaign; the
        #: change is announced on ``bus.campaign_changed``.
        self.campaign_id = campaign_id
        #: Where this session is reachable, while it is being hosted. Set by
        #: the Table panel, which owns the server, and read by anything that
        #: needs to hand somebody an address -- an invite, most of all. Empty
        #: when nobody is hosting, which is a state callers must expect rather
        #: than a reason to fail.
        self.session_address = ""


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
