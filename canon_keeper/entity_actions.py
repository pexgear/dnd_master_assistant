"""What right-clicking a creature offers, wherever it is shown.

A character turns up in several panels, and the useful things you can do to one
are mostly the same in all of them: invite somebody to play them, look at their
sheet, share them with the party. Before this, each panel grew its own menu --
which meant a menu in exactly one panel, and everything else reachable only
through a dialog somebody had to know about.

So the menu is assembled rather than written. Two halves, in this order:

**What this panel can do**, first, because it is the reason you right-clicked
*here*. "Take off the map" belongs to the Combat panel and belongs at the top of
its menu; it makes no sense anywhere else and it is not offered anywhere else.

**What is true of the creature anywhere**, after a separator. Inviting a player
to a character is the same act in the Characters panel, in the initiative order,
or in a third-party panel nobody has written yet.

Panels append the second half with one call::

    entity_actions.fill(menu, ctx, target, skip={"invite"})

Two ways to say no, because there are two reasons to:

* **the panel** passes ``skip`` -- this action makes no sense *here*;
* **the action** answers :meth:`EntityAction.applies` -- this action makes no
  sense *for this creature, for this person, right now*.

**Target carries an id, a kind and a name, and not an entity row.** A player's
panels are built from what the host sent them; there is no entity table on their
machine. An action handed a DM's ``Entity`` would work in every test and fail on
half the laptops at the table. Anything more than those three things is fetched
through ``ctx``, which is where the DM/player split is already dealt with.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from canon_keeper.plugin import AppContext

log = logging.getLogger("canonkeeper.entity_actions")

#: The entry-point group scanned at startup. A third-party package declares::
#:
#:     [project.entry-points."canonkeeper.entity_actions"]
#:     send_to_vtt = "my_pkg.actions:SendToVirtualTabletop"
ENTRY_POINT_GROUP = "canonkeeper.entity_actions"


@dataclass(frozen=True)
class Target:
    """The creature that was right-clicked, and who is asking."""

    entity_id: int
    #: "pc", "npc", "location", "faction", "item".
    kind: str
    name: str
    #: The panel that raised the menu, by its plugin id. An action may use this
    #: to word itself differently; it is not how a panel refuses one -- that is
    #: ``skip``, at the call site, where the refusal is visible.
    panel: str = ""
    #: Whatever that panel knows and an action might want: the combatant id in
    #: a fight, the square it is standing on. Panel-specific by definition, so
    #: anything reading it should cope with the key being absent.
    extra: dict = field(default_factory=dict)


@runtime_checkable
class EntityAction(Protocol):
    """One item that can appear in a creature's menu.

    Instantiated with no arguments, like a panel plugin, so keep ``__init__``
    trivial: a failure there costs the whole menu rather than one item.
    """

    #: Stable and unique. This is the name a panel passes to ``skip``, so
    #: changing it silently re-enables the action everywhere it was refused.
    id: str
    #: Where it sits among the general actions. Lower is higher up.
    order: int

    def label(self, ctx: "AppContext", target: Target) -> str:
        """What the menu says. May depend on the creature -- "Invite a player"
        against "Invite somebody else"."""
        ...

    def applies(self, ctx: "AppContext", target: Target) -> bool:
        """Whether to offer it at all, for this creature and this person."""
        ...

    def run(self, ctx: "AppContext", target: Target, parent=None) -> None:
        """Do it. ``parent`` is a widget to hang any dialog off."""
        ...


_registry: list[EntityAction] = []
_loaded = False


def register(action: EntityAction) -> None:
    """Add an action. Later registrations of the same id replace earlier ones.

    Replacing rather than appending so a plugin can deliberately override a
    first-party action, and so a double import cannot produce two identical
    menu items.
    """
    global _registry
    _registry = [existing for existing in _registry if existing.id != action.id]
    _registry.append(action)


def clear(*, sealed: bool = True) -> None:
    """Forget every registered action.

    For tests, which must not leak actions into one another. ``sealed`` leaves
    the registry closed to discovery, so a test sees exactly what it registered
    and nothing that happens to ship with the app -- pass ``sealed=False`` to
    test discovery itself.
    """
    global _registry, _loaded
    _registry = []
    _loaded = sealed


def discover(log_to: logging.Logger | None = None) -> list[EntityAction]:
    """First-party actions plus anything a plugin declared.

    The same rules the panel loader follows, for the same reason: one bad
    action must not cost the menu. An entry point that will not import, or
    resolves to something that is not an action, is logged and skipped.
    """
    global _loaded
    from importlib.metadata import entry_points

    reporter = log_to or log
    if not _loaded:
        _register_first_party()
        for entry in entry_points(group=ENTRY_POINT_GROUP):
            try:
                made = entry.load()()
            except Exception:  # noqa: BLE001 - a bad plugin is not fatal
                reporter.exception("could not load entity action %r", entry.name)
                continue
            if not isinstance(made, EntityAction):
                reporter.warning(
                    "%r is not an entity action; skipping", entry.name
                )
                continue
            register(made)
        _loaded = True
    return sorted(_registry, key=lambda a: (a.order, a.id))


def available(
    ctx: "AppContext", target: Target, skip: set[str] | None = None
) -> list[EntityAction]:
    """The actions to show for this creature, in order.

    An action that raises while deciding is dropped rather than allowed to
    empty the menu -- being wrong about whether to appear is not a reason for
    nothing to appear.
    """
    refused = skip or set()
    offered = []
    for action in discover(getattr(ctx, "log", None)):
        if action.id in refused:
            continue
        try:
            if action.applies(ctx, target):
                offered.append(action)
        except Exception:  # noqa: BLE001 - see above
            log.exception("entity action %r failed while deciding", action.id)
    return offered


def fill(menu, ctx: "AppContext", target: Target, skip: set[str] | None = None):
    """Append the general actions to a menu, under a separator.

    Called after the panel has added its own, so what belongs to this panel is
    at the top where the eye lands. Adds nothing -- not even the separator --
    when there is nothing to add.
    """
    offered = available(ctx, target, skip)
    if not offered:
        return menu
    if not menu.isEmpty():
        menu.addSeparator()
    for action in offered:
        item = menu.addAction(action.label(ctx, target))
        item.triggered.connect(
            lambda _checked=False, chosen=action: _run(chosen, ctx, target, menu)
        )
    return menu


def _run(action: EntityAction, ctx: "AppContext", target: Target, parent) -> None:
    """One action, with its failure kept to itself.

    A third-party action that raises should look like a menu item that did
    nothing, not like the app falling over on a right-click.
    """
    try:
        action.run(ctx, target, parent)
    except Exception:  # noqa: BLE001 - a bad plugin is not fatal
        log.exception("entity action %r failed", action.id)
        bus = getattr(ctx, "bus", None)
        if bus is not None:
            bus.status_message.emit(f"{action.id} could not be carried out.")


def _register_first_party() -> None:
    """The actions that ship with the app.

    Imported here rather than at module scope: they reach back into panels and
    dialogs, and this module is imported by those.
    """
    from canon_keeper.panels.actions import FIRST_PARTY

    for action in FIRST_PARTY:
        register(action())
