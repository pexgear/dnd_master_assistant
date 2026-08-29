"""The Sheet tab: derivations on screen, and the traps around saving one."""

from __future__ import annotations

import pytest

from canon_keeper.panels.characters.widget import CharactersWidget
from canon_keeper.repo.entities import KIND_PC, Entity


@pytest.fixture
def panel(ctx, qtbot) -> CharactersWidget:
    widget = CharactersWidget(ctx)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def elara(ctx, panel):
    """A character selected in the panel, with a blank sheet."""
    entity = ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_PC, name="Elara")
    )
    panel.reload(keep_selection=False)
    panel._select_entity(entity.id)
    panel._sheet_tab._create_sheet()
    return ctx.repos.entities.get(entity.id)


def _build_wizard(sheet_tab):
    sheet_tab._species.setCurrentIndex(sheet_tab._species.findData("elf"))
    sheet_tab._subspecies.setCurrentIndex(sheet_tab._subspecies.findData("high-elf"))
    sheet_tab._class.setCurrentIndex(sheet_tab._class.findData("wizard"))
    sheet_tab._level.setValue(5)
    for ability, score in (
        ("str", 8), ("dex", 14), ("con", 14), ("int", 15), ("wis", 12), ("cha", 10)
    ):
        sheet_tab._ability_inputs[ability].setValue(score)


# ----------------------------------------------------------------------- layout


def test_the_detail_pane_has_both_tabs(panel):
    assert [panel._tabs.tabText(i) for i in range(panel._tabs.count())] == [
        "Story",
        "Sheet",
    ]


def test_a_character_without_a_sheet_is_offered_one(ctx, panel):
    ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_PC, name="Brakk")
    )
    panel.reload(keep_selection=False)
    panel._list.setCurrentRow(0)

    assert panel._sheet_tab._create_button.isHidden() is False
    assert panel._sheet_tab._save_button.isHidden() is True


def test_creating_a_sheet_swaps_the_prompt_for_the_form(panel, elara):
    assert panel._sheet_tab._create_button.isHidden() is True
    assert panel._sheet_tab._save_button.isHidden() is False


# ------------------------------------------------------------------ derivations


def test_the_numbers_appear_before_saving(panel, elara):
    """Derived, not stored: the sheet answers while you are still typing."""
    tab = panel._sheet_tab
    _build_wizard(tab)

    assert tab._ability_totals["dex"].text() == "16 (+3)", "elf +2 dexterity"
    assert tab._ability_totals["int"].text() == "16 (+3)", "high elf +1 intelligence"
    assert tab._hp_max.text() == "of 32"
    assert tab._derived_labels["ac"].text() == "13"
    assert tab._derived_labels["proficiency_bonus"].text() == "+3"


def test_proficient_saves_are_marked(panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)

    assert tab._save_labels["int"].text().endswith("*"), "wizards save on intelligence"
    assert tab._save_labels["wis"].text().endswith("*")
    assert not tab._save_labels["dex"].text().endswith("*")


def test_ticking_a_skill_adds_the_proficiency_bonus(panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    before = tab._skill_labels["arcana"].text()

    tab._skill_checks["arcana"].setChecked(True)

    assert before == "+3"
    assert tab._skill_labels["arcana"].text() == "+6"


def test_spellcasting_appears_only_for_casters(panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    assert tab._spell_box.isHidden() is False
    assert "save DC 14" in tab._spell_summary.text()
    assert "level 3: 2" in tab._slots.text()

    tab._class.setCurrentIndex(tab._class.findData("fighter"))
    assert tab._spell_box.isHidden() is True


def test_hit_points_follow_the_maximum_while_at_full(panel, elara):
    """A character at full health should not be stranded on the old number."""
    tab = panel._sheet_tab
    _build_wizard(tab)
    assert tab._hp_current.value() == 32

    tab._ability_inputs["con"].setValue(18)

    assert tab._hp_max.text() == "of 42"
    assert tab._hp_current.value() == 42


def test_a_wounded_character_stays_wounded(panel, elara):
    """The rule is 'follow the maximum when at full', not 'always heal'."""
    tab = panel._sheet_tab
    _build_wizard(tab)
    tab._hp_current.setValue(5)

    tab._ability_inputs["con"].setValue(18)

    assert tab._hp_current.value() == 5


def test_subspecies_follow_the_species(panel, elara):
    tab = panel._sheet_tab
    tab._species.setCurrentIndex(tab._species.findData("elf"))
    assert tab._subspecies.findData("high-elf") >= 0

    tab._species.setCurrentIndex(tab._species.findData("dwarf"))
    assert tab._subspecies.findData("high-elf") == -1


# --------------------------------------------------------------------- saving


def test_saving_stores_the_sheet(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    tab._skill_checks["arcana"].setChecked(True)

    assert tab.save() is True

    sheet = ctx.repos.entities.get(elara.id).data["sheet"]
    assert sheet["class_index"] == "wizard"
    assert sheet["level"] == 5
    assert "arcana" in sheet["skill_proficiencies"]


def test_saving_an_illegal_sheet_is_refused(ctx, panel, elara, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    tab = panel._sheet_tab
    _build_wizard(tab)
    # Reach past the spin box, as a modified client or a bad migration could.
    tab._sheet["skill_proficiencies"] = ["arcana"]
    tab._gather = lambda: {**tab._sheet, "level": 99}

    assert tab.save() is False
    assert "level" in tab._problems.text()


def test_creating_a_sheet_does_not_undo_an_edit_on_the_story_tab(ctx, panel):
    """The sheet tab held a stale copy and wrote it back, losing the name."""
    entity = ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_PC, name="New character")
    )
    panel.reload(keep_selection=False)
    panel._select_entity(entity.id)

    panel._name.setText("Elara")
    panel._mark_dirty()
    panel.save_current()

    panel._sheet_tab._create_sheet()

    assert ctx.repos.entities.get(entity.id).name == "Elara"


def test_saving_the_sheet_does_not_undo_a_story_edit(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)

    # Something changes the summary between loading the sheet and saving it.
    meanwhile = ctx.repos.entities.get(elara.id)
    meanwhile.summary = "A tired cleric"
    ctx.repos.entities.update(meanwhile)

    tab.save()

    assert ctx.repos.entities.get(elara.id).summary == "A tired cleric"


def test_the_list_says_what_a_character_is(ctx, panel, elara):
    tab = panel._sheet_tab
    _build_wizard(tab)
    tab.save()

    labels = [panel._list.item(i).text() for i in range(panel._list.count())]
    assert any("Level 5 Elf Wizard" in label for label in labels)


def test_saving_bumps_the_version(ctx, panel, elara):
    before = ctx.repos.entities.get(elara.id).version
    _build_wizard(panel._sheet_tab)
    panel._sheet_tab.save()

    assert ctx.repos.entities.get(elara.id).version > before
