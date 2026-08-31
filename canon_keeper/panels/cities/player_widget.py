"""Places, as a player sees them.

The same reasoning as the player Characters view: a separate widget fed by
already-filtered data, so there is nothing here to accidentally reveal. A place
the DM has not shared is not dimmed or locked -- it is simply absent.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from canon_keeper.plugin import AppContext

_ID_ROLE = 256


class PlayerCitiesWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._current_id: int | None = None
        self._loading = False

        self._build_ui()
        ctx.shared.changed.connect(self._on_shared_changed)
        self.reload()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.currentItemChanged.connect(self._on_selection_changed)
        splitter.addWidget(self._tree)

        self._detail = QWidget()
        form = QFormLayout(self._detail)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._name = QLabel()
        self._name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Name", self._name)

        self._type = QLabel()
        form.addRow("Type", self._type)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        form.addRow("", self._summary)

        self._notes = QPlainTextEdit()
        self._notes.setReadOnly(True)
        self._notes.setMinimumHeight(90)
        form.addRow("What you know", self._notes)

        self._occupants = QListWidget()
        self._occupants.setMaximumHeight(140)
        form.addRow("Who is here", self._occupants)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._hint = QLabel("Join a session to see where you have been.")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        right_layout.addWidget(self._hint)
        right_layout.addWidget(self._detail, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)
        self._show_detail(False)

    def _show_detail(self, visible: bool) -> None:
        self._detail.setVisible(visible)
        self._hint.setVisible(not visible)

    # ------------------------------------------------------------------ loads

    def _on_shared_changed(self) -> None:
        """The host sent something. Reload, and say so if nobody is looking."""
        self.reload()
        self._ctx.bus.panel_attention.emit("cities")

    def reload(self) -> None:
        previous = self._current_id
        self._loading = True
        self._tree.clear()
        selected: QTreeWidgetItem | None = None

        def add(parent_item, parent_id) -> None:
            nonlocal selected
            for place in self._ctx.shared.children_of(parent_id):
                label = place.get("name", "?")
                place_type = place.get("data", {}).get("place_type", "")
                if place_type:
                    label += f"   [{place_type}]"
                item = QTreeWidgetItem([label])
                item.setData(0, _ID_ROLE, place["id"])
                if parent_item is None:
                    self._tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)
                if place["id"] == previous:
                    selected = item
                add(item, place["id"])

        add(None, None)
        self._tree.expandAll()
        self._loading = False

        if self._tree.topLevelItemCount() == 0:
            self._current_id = None
            self._show_detail(False)
            self._hint.setText(
                "Nowhere yet. Places appear here as the DM shares them."
            )
        elif selected is not None:
            self._tree.setCurrentItem(selected)
            # Same row, so no selection change fires; re-read it by hand or the
            # pane keeps showing what the host sent last time.
            place = self._ctx.shared.get(self._current_id)
            if place is not None:
                self._load(place)
        elif self._tree.currentItem() is None:
            self._tree.setCurrentItem(self._tree.topLevelItem(0))

    def _on_selection_changed(self, current, _previous) -> None:
        if self._loading or current is None:
            return
        place = self._ctx.shared.get(current.data(0, _ID_ROLE))
        if place is None:
            return
        self._load(place)

    def _load(self, place: dict) -> None:
        self._current_id = place["id"]
        data = place.get("data", {})
        self._name.setText(place.get("name", ""))
        self._type.setText(str(data.get("place_type", "")))
        self._summary.setText(place.get("summary", ""))
        self._notes.setPlainText(str(data.get("shared_notes", "")))

        self._occupants.clear()
        for occupant in self._ctx.shared.occupants_of(place["id"]):
            status = occupant.get("data", {}).get("status", "")
            label = occupant.get("name", "?") + (f"  -  {status}" if status else "")
            item = QListWidgetItem(label)
            item.setData(_ID_ROLE, occupant["id"])
            self._occupants.addItem(item)

        self._show_detail(True)
