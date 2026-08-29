"""What each panel is called: your name, the party's, and the default."""

from __future__ import annotations

import logging

import pytest

from canon_keeper.bus import Bus
from canon_keeper.naming import PanelNames, clean
from canon_keeper.plugin import AppContext
from canon_keeper.shell.rename_panels import COL_MINE, COL_PARTY, RenamePanelsDialog


@pytest.fixture
def names(repos):
    panel_names = PanelNames(repos.settings, is_dm=True)
    panel_names.register("table", "Table")
    panel_names.register("characters", "Characters")
    return panel_names


@pytest.fixture
def player_names(repos):
    panel_names = PanelNames(repos.settings, is_dm=False)
    panel_names.register("table", "Table")
    return panel_names


# ------------------------------------------------------------------ resolution


def test_the_default_is_used_when_nothing_is_set(names):
    assert names.resolve("table") == "Table"


def test_the_party_name_beats_the_default(names):
    names.set_party("table", "The Tavern")
    assert names.resolve("table") == "The Tavern"


def test_your_own_name_beats_the_party(names):
    """An explicit personal choice is the most specific thing we know."""
    names.set_party("table", "The Tavern")
    names.set_local("table", "Chat")
    assert names.resolve("table") == "Chat"


def test_clearing_your_name_falls_back_to_the_party(names):
    names.set_party("table", "The Tavern")
    names.set_local("table", "Chat")
    names.set_local("table", "")
    assert names.resolve("table") == "The Tavern"


def test_clearing_the_party_name_falls_back_to_the_default(names):
    names.set_party("table", "The Tavern")
    names.set_party("table", "")
    assert names.resolve("table") == "Table"


def test_an_unregistered_panel_still_gets_a_name(names):
    """A third-party panel we know nothing about must not render as blank."""
    assert names.resolve("weather") == "weather"


def test_names_are_tidied(names):
    names.set_local("table", "  The   Long  Table \n")
    assert names.resolve("table") == "The Long Table"


def test_a_name_cannot_be_enormous(names):
    names.set_local("table", "x" * 500)
    assert len(names.resolve("table")) == 40


def test_whitespace_only_is_the_same_as_clearing(names):
    names.set_party("table", "The Tavern")
    names.set_local("table", "   ")
    assert names.resolve("table") == "The Tavern"


@pytest.mark.parametrize("value", [None, "", "   ", "\n"])
def test_clean_treats_empty_things_alike(value):
    assert clean(value) == ""


def test_the_tooltip_explains_where_the_name_came_from(names):
    names.set_party("table", "The Tavern")
    names.set_local("table", "Chat")

    described = names.describe("table")

    assert "Default: Table" in described
    assert "The Tavern" in described
    assert "Chat" in described


# --------------------------------------------------------------------- players


def test_a_player_cannot_set_a_party_name(player_names):
    """Their copy of the campaign is someone else's; it is not theirs to write."""
    assert player_names.set_party("table", "Mine Now") is False
    assert player_names.resolve("table") == "Table"


def test_a_player_takes_the_party_names_off_the_wire(player_names):
    player_names.apply_party_names({"table": "The Tavern"})
    assert player_names.resolve("table") == "The Tavern"


def test_a_player_can_still_rename_it_for_themselves(player_names):
    player_names.apply_party_names({"table": "The Tavern"})
    player_names.set_local("table", "Chat")
    assert player_names.resolve("table") == "Chat"


def test_rubbish_off_the_wire_is_ignored(player_names):
    player_names.apply_party_names({"table": "   ", "other": None})
    player_names.apply_party_names("not a mapping")
    assert player_names.resolve("table") == "Table"


def test_a_change_is_announced(player_names, qtbot):
    with qtbot.waitSignal(player_names.changed, timeout=1000):
        player_names.apply_party_names({"table": "The Tavern"})


def test_receiving_the_same_names_again_is_quiet(player_names):
    """Otherwise every reconnect would re-title every dock for no reason."""
    player_names.apply_party_names({"table": "The Tavern"})
    fired = []
    player_names.changed.connect(lambda: fired.append(1))

    player_names.apply_party_names({"table": "The Tavern"})

    assert fired == []


# --------------------------------------------------------------- what is shared


def test_only_the_party_names_are_published(names):
    names.set_party("table", "The Tavern")
    names.set_local("characters", "My People")

    published = names.party_names()

    assert published == {"table": "The Tavern"}
    assert "characters" not in published, "a private rename must not be broadcast"


# ---------------------------------------------------------------------- dialog


def _ctx(repos, panel_names, role="dm"):
    return AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=1,
        role=role,
        names=panel_names,
    )


def test_the_dialog_shows_every_panel(repos, names, qtbot):
    dialog = RenamePanelsDialog(_ctx(repos, names), None)
    qtbot.addWidget(dialog)
    assert dialog._table.rowCount() == 2


def test_editing_the_dialog_renames(repos, names, qtbot):
    dialog = RenamePanelsDialog(_ctx(repos, names), None)
    qtbot.addWidget(dialog)

    row = next(
        r
        for r in range(dialog._table.rowCount())
        if dialog._table.item(r, 0).text() == "Table"
    )
    dialog._table.item(row, COL_MINE).setText("Chat")
    dialog._table.item(row, COL_PARTY).setText("The Tavern")
    party_changed = dialog.apply()

    assert names.local("table") == "Chat"
    assert names.party("table") == "The Tavern"
    assert party_changed is True


def test_clearing_my_names_leaves_the_party_alone(repos, names, qtbot):
    names.set_party("table", "The Tavern")
    names.set_local("table", "Chat")

    dialog = RenamePanelsDialog(_ctx(repos, names), None)
    qtbot.addWidget(dialog)
    dialog._clear_mine()
    dialog.apply()

    assert names.local("table") == ""
    assert names.party("table") == "The Tavern"


def test_a_player_cannot_edit_the_party_column(repos, player_names, qtbot):
    from PySide6.QtCore import Qt

    dialog = RenamePanelsDialog(_ctx(repos, player_names, role="player"), None)
    qtbot.addWidget(dialog)

    party_cell = dialog._table.item(0, COL_PARTY)
    assert not (party_cell.flags() & Qt.ItemFlag.ItemIsEditable)
    assert dialog._table.item(0, COL_MINE).flags() & Qt.ItemFlag.ItemIsEditable


def test_a_players_edits_never_report_a_party_change(repos, player_names, qtbot):
    dialog = RenamePanelsDialog(_ctx(repos, player_names, role="player"), None)
    qtbot.addWidget(dialog)
    dialog._table.item(0, COL_MINE).setText("Chat")

    assert dialog.apply() is False
    assert player_names.local("table") == "Chat"
