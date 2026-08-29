"""The signal bus that keeps panels from importing one another.

Panels never talk directly. Characters emits ``active_entity_changed``; the
Conversation panel hears it and retargets. That indirection is what makes the
plugin system real rather than decorative — a third-party panel gets exactly the
same wiring the first-party ones use.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Bus(QObject):
    """Application-wide events. Every payload is a row id, or -1 for 'none'."""

    # An entity was created, edited or deleted.
    entity_changed = Signal(int)
    entity_deleted = Signal(int)

    # Selection: what the DM is looking at right now.
    active_entity_changed = Signal(int)
    active_location_changed = Signal(int)

    # Who may see an entity changed. The host republishes it, so a revoked
    # share actually takes the thing off the player's screen.
    share_changed = Signal(int)

    # A player asking the host to change their own character. The host decides;
    # the change only becomes real when it is echoed back.
    player_edit_requested = Signal(int, dict)

    # A panel was renamed, by you or by the DM. The shell re-titles the docks.
    panel_names_changed = Signal()

    # Canon.
    fact_committed = Signal(int)
    utterance_added = Signal(int)

    # The open campaign was switched.
    campaign_changed = Signal(int)

    # Light/dark switched. True when the new appearance is dark. Panels that
    # paint their own colours -- syntax highlighting, charts -- should listen.
    theme_changed = Signal(bool)

    # Free-text status for the main window's status bar.
    status_message = Signal(str)
