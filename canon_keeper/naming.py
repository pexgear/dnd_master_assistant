"""What each panel is called.

Three layers, in order of precedence:

1. **Your name** -- what you renamed it to on this machine. An explicit personal
   choice, so it wins.
2. **The party name** -- what the DM calls it for this campaign, sent to
   everyone in the session. "Cities & Places" becomes "The Sword Coast".
3. **The default** -- whatever the panel ships with.

Clearing your own name falls back to the party's, and clearing that falls back
to the default, so nothing is ever unnamed.

The DM's names live in the campaign, because they are part of the campaign's
flavour. Yours live wherever your settings live -- your profile if you are a
player, the campaign if you are running it -- which needs no special case: it is
simply whichever database you opened.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

LOCAL_PREFIX = "panel_name.local."
PARTY_PREFIX = "panel_name.party."

MAX_LENGTH = 40


def clean(name: str | None) -> str:
    """A name is one line and not enormous. Blank means 'no override'."""
    if not name:
        return ""
    return " ".join(str(name).split())[:MAX_LENGTH].strip()


class PanelNames(QObject):
    """Resolves panel titles, and remembers the overrides."""

    changed = Signal()

    def __init__(self, settings, is_dm: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._is_dm = is_dm
        self._defaults: dict[str, str] = {}
        # For a player these arrive over the wire rather than from a database:
        # the campaign is not theirs to read.
        self._received_party: dict[str, str] = {}

    # ------------------------------------------------------------- registration

    def register(self, panel_id: str, default_title: str) -> None:
        self._defaults[panel_id] = default_title

    @property
    def panel_ids(self) -> list[str]:
        return list(self._defaults)

    def default(self, panel_id: str) -> str:
        return self._defaults.get(panel_id, panel_id)

    # ------------------------------------------------------------------ reading

    def local(self, panel_id: str) -> str:
        return clean(self._settings.get(LOCAL_PREFIX + panel_id, ""))

    def party(self, panel_id: str) -> str:
        if self._is_dm:
            return clean(self._settings.get(PARTY_PREFIX + panel_id, ""))
        return clean(self._received_party.get(panel_id, ""))

    def resolve(self, panel_id: str) -> str:
        """The name to actually show."""
        return (
            self.local(panel_id)
            or self.party(panel_id)
            or self.default(panel_id)
        )

    def describe(self, panel_id: str) -> str:
        """A tooltip explaining where the shown name came from."""
        parts = [f"Default: {self.default(panel_id)}"]
        party = self.party(panel_id)
        if party:
            parts.append(f"The party calls it: {party}")
        mine = self.local(panel_id)
        if mine:
            parts.append(f"You call it: {mine}")
        return "\n".join(parts)

    def party_names(self) -> dict[str, str]:
        """The DM's names, for sending to the table."""
        if not self._is_dm:
            return dict(self._received_party)
        return {
            panel_id: name
            for panel_id in self._defaults
            if (name := self.party(panel_id))
        }

    # ------------------------------------------------------------------ writing

    def set_local(self, panel_id: str, name: str | None) -> None:
        """Rename it for yourself. Blank clears the override."""
        self._settings.set(LOCAL_PREFIX + panel_id, clean(name))
        self.changed.emit()

    def set_party(self, panel_id: str, name: str | None) -> bool:
        """Rename it for everyone. Only the campaign's owner may.

        Returns False for a player, whose copy of the campaign is a view of
        someone else's and not a place to write.
        """
        if not self._is_dm:
            return False
        self._settings.set(PARTY_PREFIX + panel_id, clean(name))
        self.changed.emit()
        return True

    def apply_party_names(self, names: dict) -> None:
        """Take the DM's names off the wire."""
        if not isinstance(names, dict):
            return
        cleaned = {
            str(key): clean(value)
            for key, value in names.items()
            if clean(value)
        }
        if cleaned != self._received_party:
            self._received_party = cleaned
            self.changed.emit()
