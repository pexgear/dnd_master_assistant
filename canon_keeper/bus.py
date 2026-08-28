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

    # Canon.
    fact_committed = Signal(int)
    utterance_added = Signal(int)

    # The open campaign was switched.
    campaign_changed = Signal(int)

    # Free-text status for the main window's status bar.
    status_message = Signal(str)
