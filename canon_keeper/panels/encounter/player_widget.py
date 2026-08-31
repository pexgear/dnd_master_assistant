"""A player's view of the fight: the same map, and nothing to press.

Built from ``ctx.shared`` -- what the host chose to send -- rather than from a
database this app does not have. So the reason a player cannot see the goblin
in the corner is not that this widget declines to draw it: the goblin is not in
the bytes.

Read-only is not a disabled button here, it is the absence of a way to ask.
Moving a token is a message this widget never sends, and the host would refuse
it if it did.

While a turn is waiting on you, this panel and the chat box are held -- and
deliberately nothing else. Your character sheet, the places you know and the
rest of the app stay open, because being asked what your character does is not
a reason to stop being able to look anything up.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from canon_keeper.panels.encounter.grid import GridMap, Preview, Token
from canon_keeper.plugin import AppContext
from canon_keeper.repo.entities import KIND_PC


class PlayerEncounterWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self._heading = QLabel("")
        self._heading.setWordWrap(True)
        outer.addWidget(self._heading)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._order = QListWidget()
        self._order.setMaximumWidth(230)
        self._order.setToolTip("The initiative order, as far as you can see it")
        splitter.addWidget(self._order)

        self._map = GridMap()
        self._map.read_only = True
        splitter.addWidget(self._map)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)

        # What is on offer, in words, above the map that is showing it in dots.
        # No buttons: answering happens where the player already is, in the
        # chat box they typed the turn into. Two places to press yes is one
        # place too many.
        self._offer_text = QLabel("")
        self._offer_text.setWordWrap(True)
        self._offer_text.setVisible(False)
        outer.addWidget(self._offer_text)

        row = QHBoxLayout()
        self._note = QLabel("")
        self._note.setWordWrap(True)
        row.addWidget(self._note, 1)
        outer.addLayout(row)

        #: The turn we are being asked about, if any.
        self._pending: dict | None = None
        ctx.bus.action_proposed.connect(self._on_offered)
        ctx.bus.action_withdrawn.connect(self._on_withdrawn)

        if ctx.shared is not None:
            ctx.shared.encounter_changed.connect(self._refresh)
            # Names and hit points come off the entities, so those moving
            # changes what this shows too.
            ctx.shared.changed.connect(self._refresh)
        ctx.bus.theme_changed.connect(lambda _dark: self._map.update())

        self._refresh()

    def _refresh(self) -> None:
        encounter = self._ctx.shared.encounter if self._ctx.shared is not None else None
        if not encounter:
            self._heading.setText("No fight right now.")
            self._note.setText("")
            self._order.clear()
            self._map.set_grid(1, 1)
            self._map.set_obstacles(())
            self._map.set_tokens([])
            return

        combatants = [c for c in encounter.get("combatants", []) if isinstance(c, dict)]
        turn = encounter.get("turn")

        self._map.set_grid(
            int(encounter.get("width") or 1), int(encounter.get("height") or 1)
        )
        # Terrain reaches everyone. The reason to draw a pillar on a player's
        # map is so they can decide to stand behind it.
        self._map.set_obstacles(
            square
            for square in encounter.get("obstacles") or ()
            if isinstance(square, (list, tuple)) and len(square) == 2
        )
        self._map.set_tokens(
            [
                Token(
                    id=int(c.get("id") or 0),
                    label=self._name_of(c),
                    x=int(c["x"]),
                    y=int(c["y"]),
                    ours=self._is_pc(c),
                    is_turn=c.get("id") == turn,
                )
                for c in combatants
                if isinstance(c.get("x"), int) and isinstance(c.get("y"), int)
            ]
        )

        self._order.clear()
        for combatant in combatants:
            initiative = combatant.get("initiative")
            label = "  ".join(
                part
                for part in (
                    "  -- " if initiative is None else f"{int(initiative):>4}",
                    self._name_of(combatant),
                    self._hp_of(combatant),
                    "" if _placed(combatant) else "- off the map",
                )
                if part
            )
            item = QListWidgetItem(label)
            if combatant.get("id") == turn:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self._order.addItem(item)

        name = encounter.get("name") or "The fight"
        round_number = int(encounter.get("round") or 0)
        if round_number:
            up = self._up(combatants, turn)
            self._heading.setText(f"<b>{name}</b> -- round {round_number}, {up}.")
        else:
            self._heading.setText(f"<b>{name}</b> -- not started yet.")

        # Said plainly rather than left to be inferred from a short list. A
        # player counting four goblins on the DM's side of the screen and three
        # on theirs should know which of the two is the truth.
        self._note.setText(
            "You see what your DM has shared with you. There may be more in the "
            "room than is on this map."
        )

    # ------------------------------------------------------------- your turn

    def _on_offered(self, action: dict) -> None:
        """A turn worked out for you, waiting on your word.

        The bar says it in words and the map says it in dots and a ghost. Both,
        because the words are what you check and the map is what you recognise.
        """
        self._pending = action
        watching = bool(action.get("watching"))
        self._offer_text.setText(
            f"<b>{action.get('text', 'A turn.')}</b> "
            + (
                "-- waiting on them."
                if watching
                else "-- confirm it in the chat, or say what you meant instead."
            )
        )
        self._offer_text.setVisible(True)
        # Held, not greyed: the map is showing the very thing being decided,
        # so it has to stay readable. It just stops taking clicks.
        self._map.frozen = not watching
        self._order.setEnabled(watching)
        self._show_preview()

    def _on_withdrawn(self, action_id: str) -> None:
        if self._pending and self._pending.get("id") == action_id:
            self._clear_offer()

    def _clear_offer(self) -> None:
        self._pending = None
        self._offer_text.setVisible(False)
        self._map.frozen = False
        self._order.setEnabled(True)
        self._map.set_preview(None)

    def _show_preview(self) -> None:
        action = self._pending
        if not action:
            self._map.set_preview(None)
            return
        move = action.get("move")
        self._map.set_preview(
            Preview(
                token=int(action.get("combatant") or 0),
                to=(int(move[0]), int(move[1])) if move else None,
                target=self._square_of(action.get("target")),
            )
        )

    def _square_of(self, combatant_id) -> tuple[int, int] | None:
        fight = self._ctx.shared.encounter if self._ctx.shared is not None else None
        for combatant in (fight or {}).get("combatants") or []:
            if combatant.get("id") == combatant_id:
                x, y = combatant.get("x"), combatant.get("y")
                if isinstance(x, int) and isinstance(y, int):
                    return x, y
        return None

    # ---------------------------------------------------------------- helpers

    def _entity(self, combatant: dict) -> dict | None:
        entity_id = combatant.get("entity")
        if not isinstance(entity_id, int) or self._ctx.shared is None:
            return None
        return self._ctx.shared.get(entity_id)

    def _name_of(self, combatant: dict) -> str:
        entity = self._entity(combatant)
        return (entity or {}).get("name") or "someone"

    def _is_pc(self, combatant: dict) -> bool:
        entity = self._entity(combatant)
        return bool(entity) and entity.get("kind") == KIND_PC

    def _hp_of(self, combatant: dict) -> str:
        entity = self._entity(combatant)
        data = (entity or {}).get("data") or {}
        hp, max_hp = data.get("hp"), data.get("max_hp")
        if isinstance(hp, int) and isinstance(max_hp, int):
            return f"({hp}/{max_hp})"
        return ""

    def _up(self, combatants: list[dict], turn) -> str:
        for combatant in combatants:
            if combatant.get("id") == turn:
                return f"{self._name_of(combatant)} is up"
        # The turn belongs to something we were not sent. Saying so is more
        # honest than a blank line, and it is what the DM would say out loud.
        return "something you cannot see is up"


def _placed(combatant: dict) -> bool:
    return isinstance(combatant.get("x"), int) and isinstance(combatant.get("y"), int)
