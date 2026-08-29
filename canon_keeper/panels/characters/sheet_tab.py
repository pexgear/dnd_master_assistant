"""The character sheet, as a tab beside the story.

Everything derived is recomputed as you type, so the sheet argues with you
immediately rather than after saving: raise constitution and the hit points move
while you watch. That is the whole benefit of deriving rather than storing, made
visible.

What is editable depends on who you are. The DM edits anything. A player edits
their own characters, and the *build* half of those -- level, class, ability
scores -- is proposed rather than applied, because those are changes to what the
character is rather than what is happening to it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from canon_keeper.content import Content
from canon_keeper.rules import derive
from canon_keeper.rules.sheet import ABILITIES, ABILITY_NAMES, new_sheet, sheet_of
from canon_keeper.rules.validation import validate

_NONE = "(none)"


class SheetWidget(QWidget):
    """One character's mechanical sheet."""

    #: The sheet was saved; the panel reloads and the host republishes.
    saved = Signal(int)  # entity id

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._content = Content(ctx.repos.settings)
        self._entity = None
        self._sheet: dict | None = None
        self._loading = False
        #: The maximum as of the last refresh, so a character sitting at full
        #: health follows the maximum up when constitution or level changes
        #: instead of being stranded on the old number.
        self._last_max_hp: int | None = None

        self._build_ui()
        self.set_entity(None)

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._empty = QLabel(
            "No character sheet yet.\n\nPress Create sheet to give this character "
            "one, or leave it as a purely narrative entry."
        )
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        self._create_button = QPushButton("Create sheet")
        self._create_button.clicked.connect(self._create_sheet)
        outer.addWidget(self._create_button, alignment=Qt.AlignmentFlag.AlignCenter)

        self._body = QWidget()
        body = QVBoxLayout(self._body)
        body.setContentsMargins(6, 6, 6, 6)

        body.addWidget(self._build_identity())
        body.addWidget(self._build_abilities())
        body.addWidget(self._build_derived())
        body.addWidget(self._build_saves())
        body.addWidget(self._build_skills())
        self._spell_box = self._build_spellcasting()
        body.addWidget(self._spell_box)
        body.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._body)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self._save_button = QPushButton("Save sheet")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self.save)
        buttons.addWidget(self._save_button)

        self._problems = QLabel("")
        self._problems.setWordWrap(True)
        buttons.addWidget(self._problems, 1)
        outer.addLayout(buttons)

    def _build_identity(self) -> QWidget:
        box = QGroupBox("Who they are")
        form = QFormLayout(box)

        self._species = QComboBox()
        self._species.currentIndexChanged.connect(self._on_species_changed)
        form.addRow("Species", self._species)

        self._subspecies = QComboBox()
        self._subspecies.currentIndexChanged.connect(self._changed)
        form.addRow("Subspecies", self._subspecies)

        self._class = QComboBox()
        self._class.currentIndexChanged.connect(self._on_class_changed)
        form.addRow("Class", self._class)

        self._subclass = QComboBox()
        self._subclass.currentIndexChanged.connect(self._changed)
        form.addRow("Subclass", self._subclass)

        self._level = QSpinBox()
        self._level.setRange(1, 20)
        self._level.valueChanged.connect(self._on_level_changed)
        form.addRow("Level", self._level)

        self._background = QComboBox()
        self._background.currentIndexChanged.connect(self._changed)
        form.addRow("Background", self._background)
        return box

    def _build_abilities(self) -> QWidget:
        box = QGroupBox("Ability scores")
        grid = QGridLayout(box)
        self._ability_inputs: dict[str, QSpinBox] = {}
        self._ability_totals: dict[str, QLabel] = {}

        for column, ability in enumerate(ABILITIES):
            grid.addWidget(
                QLabel(ABILITY_NAMES[ability][:3].upper()), 0, column,
                alignment=Qt.AlignmentFlag.AlignCenter,
            )

            spin = QSpinBox()
            spin.setRange(1, 30)
            spin.setToolTip(f"{ABILITY_NAMES[ability]} before species and improvements")
            spin.valueChanged.connect(self._changed)
            grid.addWidget(spin, 1, column)
            self._ability_inputs[ability] = spin

            total = QLabel("10 (+0)")
            total.setAlignment(Qt.AlignmentFlag.AlignCenter)
            total.setToolTip("With species bonuses and improvements applied")
            grid.addWidget(total, 2, column)
            self._ability_totals[ability] = total
        return box

    def _build_derived(self) -> QWidget:
        box = QGroupBox("In play")
        grid = QGridLayout(box)

        self._hp_current = QSpinBox()
        self._hp_current.setRange(-99, 999)
        self._hp_current.valueChanged.connect(self._changed)
        grid.addWidget(QLabel("Hit points"), 0, 0)
        grid.addWidget(self._hp_current, 0, 1)
        self._hp_max = QLabel("of 0")
        grid.addWidget(self._hp_max, 0, 2)

        self._derived_labels: dict[str, QLabel] = {}
        for column, (key, caption) in enumerate(
            (
                ("ac", "Armour class"),
                ("initiative", "Initiative"),
                ("speed", "Speed"),
                ("proficiency_bonus", "Proficiency"),
                ("passive_perception", "Passive perception"),
            )
        ):
            grid.addWidget(QLabel(caption), 1, column)
            value = QLabel("-")
            value.setAlignment(Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(value, 2, column)
            self._derived_labels[key] = value
        return box

    def _build_saves(self) -> QWidget:
        box = QGroupBox("Saving throws")
        grid = QGridLayout(box)
        self._save_labels: dict[str, QLabel] = {}
        for column, ability in enumerate(ABILITIES):
            grid.addWidget(
                QLabel(ABILITY_NAMES[ability][:3].upper()), 0, column,
                alignment=Qt.AlignmentFlag.AlignCenter,
            )
            value = QLabel("+0")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(value, 1, column)
            self._save_labels[ability] = value
        return box

    def _build_skills(self) -> QWidget:
        box = QGroupBox("Skills")
        grid = QGridLayout(box)
        self._skill_checks: dict[str, QCheckBox] = {}
        self._skill_labels: dict[str, QLabel] = {}

        skills = sorted(self._content.skills(), key=lambda s: s.get("name", ""))
        half = (len(skills) + 1) // 2
        for position, skill in enumerate(skills):
            index = skill["index"]
            row, column = (position % half), (position // half) * 2

            check = QCheckBox(skill.get("name", index))
            check.setToolTip(
                (skill.get("ability_score") or {}).get("name", "")
            )
            check.toggled.connect(self._changed)
            grid.addWidget(check, row, column)
            self._skill_checks[index] = check

            value = QLabel("+0")
            grid.addWidget(value, row, column + 1)
            self._skill_labels[index] = value
        return box

    def _build_spellcasting(self) -> QWidget:
        box = QGroupBox("Spellcasting")
        layout = QVBoxLayout(box)
        self._spell_summary = QLabel("-")
        layout.addWidget(self._spell_summary)
        self._slots = QLabel("-")
        self._slots.setWordWrap(True)
        layout.addWidget(self._slots)
        return box

    # ------------------------------------------------------------------ state

    def set_entity(self, entity) -> None:
        """Show one character, or nothing."""
        self._entity = entity
        self._sheet = sheet_of(entity.data) if entity is not None else None

        has_entity = entity is not None
        has_sheet = self._sheet is not None

        self._empty.setVisible(has_entity and not has_sheet)
        self._create_button.setVisible(has_entity and not has_sheet)
        self._body.parentWidget().parentWidget().setVisible(has_sheet)
        self._save_button.setVisible(has_sheet)

        if not has_entity:
            self._empty.setText("Select a character.")
            self._empty.setVisible(True)
        elif not has_sheet:
            self._empty.setText(
                "No character sheet yet.\n\nPress Create sheet to give this "
                "character one, or leave it as a purely narrative entry."
            )

        if has_sheet:
            self._populate_choices()
            self._load()

    def _create_sheet(self) -> None:
        if self._entity is None:
            return
        # Re-read first: the copy we are holding may predate an edit made on the
        # Story tab, and writing it back would quietly undo that edit.
        entity = self._ctx.repos.entities.get(self._entity.id)
        if entity is None:
            return
        entity.data["sheet"] = new_sheet()
        self._ctx.repos.entities.update(entity)
        self._ctx.bus.entity_changed.emit(entity.id)
        self.set_entity(self._ctx.repos.entities.get(entity.id))

    # ------------------------------------------------------------------ filling

    def _populate_choices(self) -> None:
        self._loading = True
        for combo, entries in (
            (self._species, self._content.species()),
            (self._class, self._content.classes()),
            (self._background, self._content.backgrounds()),
        ):
            combo.clear()
            combo.addItem(_NONE, "")
            for entry in entries:
                combo.addItem(entry.get("name", entry["index"]), entry["index"])
        self._loading = False

    def _populate_subspecies(self, species: str, chosen: str = "") -> None:
        self._subspecies.clear()
        self._subspecies.addItem(_NONE, "")
        for entry in self._content.subspecies_of(species):
            self._subspecies.addItem(entry.get("name", entry["index"]), entry["index"])
        self._select(self._subspecies, chosen)

    def _populate_subclasses(self, class_index: str, chosen: str = "") -> None:
        self._subclass.clear()
        self._subclass.addItem(_NONE, "")
        for entry in self._content.subclasses_of(class_index):
            self._subclass.addItem(entry.get("name", entry["index"]), entry["index"])
        self._select(self._subclass, chosen)

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value or "")
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _load(self) -> None:
        sheet = self._sheet or {}
        self._loading = True

        self._select(self._species, sheet.get("species", ""))
        self._populate_subspecies(sheet.get("species", ""), sheet.get("subspecies", ""))
        self._select(self._class, sheet.get("class_index", ""))
        self._populate_subclasses(sheet.get("class_index", ""), sheet.get("subclass", ""))
        self._select(self._background, sheet.get("background", ""))
        self._level.setValue(int(sheet.get("level", 1)))

        base = sheet.get("abilities") or {}
        for ability, spin in self._ability_inputs.items():
            spin.setValue(int(base.get(ability, 10)))

        proficient = set(sheet.get("skill_proficiencies") or ())
        for index, check in self._skill_checks.items():
            check.setChecked(index in proficient)

        current, maximum = derive.hit_points(sheet, self._content)
        self._hp_current.setValue(current)
        self._last_max_hp = maximum

        self._loading = False
        self._refresh()
        self._save_button.setEnabled(False)

    # ----------------------------------------------------------------- editing

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        self._save_button.setEnabled(True)
        self._refresh()

    def _on_species_changed(self, *_args) -> None:
        if not self._loading:
            self._populate_subspecies(self._species.currentData() or "")
        self._changed()

    def _on_class_changed(self, *_args) -> None:
        if not self._loading:
            self._populate_subclasses(self._class.currentData() or "")
        self._changed()

    def _on_level_changed(self, *_args) -> None:
        self._changed()

    def _gather(self) -> dict:
        """The sheet as the form currently reads, without saving it."""
        sheet = dict(self._sheet or new_sheet())
        sheet["species"] = self._species.currentData() or ""
        sheet["subspecies"] = self._subspecies.currentData() or ""
        sheet["class_index"] = self._class.currentData() or ""
        sheet["subclass"] = self._subclass.currentData() or ""
        sheet["background"] = self._background.currentData() or ""
        sheet["level"] = self._level.value()
        sheet["abilities"] = {a: s.value() for a, s in self._ability_inputs.items()}
        sheet["skill_proficiencies"] = [
            index for index, check in self._skill_checks.items() if check.isChecked()
        ]
        sheet["hp_current"] = self._hp_current.value()
        return sheet

    # ---------------------------------------------------------------- derived

    def _refresh(self) -> None:
        """Recompute everything the sheet shows. Cheap, so do it on every keystroke."""
        if self._sheet is None:
            return
        sheet = self._gather()
        summary = derive.summary(sheet, self._content)

        for ability, label in self._ability_totals.items():
            score = summary["abilities"][ability]
            label.setText(f"{score} ({derive.format_bonus(summary['modifiers'][ability])})")

        maximum = summary["hp_max"]
        if (
            self._last_max_hp is not None
            and maximum != self._last_max_hp
            and self._hp_current.value() == self._last_max_hp
        ):
            was_loading = self._loading
            self._loading = True
            self._hp_current.setValue(maximum)
            self._loading = was_loading
        self._last_max_hp = maximum

        self._hp_max.setText(f"of {maximum}")
        self._derived_labels["ac"].setText(str(summary["ac"]))
        self._derived_labels["initiative"].setText(
            derive.format_bonus(summary["initiative"])
        )
        self._derived_labels["speed"].setText(f"{summary['speed']} ft")
        self._derived_labels["proficiency_bonus"].setText(
            derive.format_bonus(summary["proficiency_bonus"])
        )
        self._derived_labels["passive_perception"].setText(
            str(summary["passive_perception"])
        )

        proficient_saves = derive.saving_throw_proficiencies(sheet, self._content)
        for ability, label in self._save_labels.items():
            text = derive.format_bonus(summary["saving_throws"][ability])
            label.setText(f"{text} *" if ability in proficient_saves else text)
            label.setToolTip(
                "Proficient" if ability in proficient_saves else "Not proficient"
            )

        for index, label in self._skill_labels.items():
            label.setText(derive.format_bonus(summary["skills"].get(index, 0)))

        self._refresh_spellcasting(sheet, summary)

    def _refresh_spellcasting(self, sheet: dict, summary: dict) -> None:
        if not derive.is_caster(sheet):
            self._spell_box.setVisible(False)
            return

        self._spell_box.setVisible(True)
        ability = derive.spellcasting_ability(sheet)
        self._spell_summary.setText(
            f"{ABILITY_NAMES[ability]} caster  ·  save DC {summary['spell_save_dc']}"
            f"  ·  attack {derive.format_bonus(summary['spell_attack_bonus'])}"
            f"  ·  {derive.cantrips_known(sheet, self._content)} cantrips"
        )
        slots = summary["spell_slots"]
        if slots:
            self._slots.setText(
                "Slots:  "
                + "   ".join(f"level {lvl}: {count}" for lvl, count in sorted(slots.items()))
            )
        else:
            self._slots.setText("No spell slots at this level.")

    # ------------------------------------------------------------------- saving

    def save(self) -> bool:
        if self._entity is None or self._sheet is None:
            return False

        sheet = self._gather()
        report = validate(sheet, self._content)
        if not report.ok:
            self._problems.setText(report.summary())
            QMessageBox.warning(self, "Character sheet", report.summary())
            return False

        self._problems.setText("")
        entity = self._ctx.repos.entities.get(self._entity.id)
        if entity is None:
            return False
        entity.data["sheet"] = sheet
        self._ctx.repos.entities.update(entity)

        self._entity = entity
        self._sheet = sheet
        self._save_button.setEnabled(False)
        self._ctx.bus.entity_changed.emit(entity.id)
        self._ctx.bus.status_message.emit(
            f"Saved {entity.name}: {derive.describe(sheet, self._content)}"
        )
        self.saved.emit(entity.id)
        return True

    @property
    def is_dirty(self) -> bool:
        return self._save_button.isEnabled()
