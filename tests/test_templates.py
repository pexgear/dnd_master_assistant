"""Campaigns that start the same way every time.

The claim worth testing is the whole feature: run a template twice and get the
same evening twice. Everything else -- the storyline, the shares, the logins --
is only useful because of that.
"""

from __future__ import annotations

import json

import pytest

from canon_keeper.db import connect, migrate
from canon_keeper.repo import Repos
from canon_keeper.templates import (
    PROGRESS_SETTING,
    SOURCE_SETTING,
    STORYLINE_SETTING,
    Template,
    TemplateError,
    available,
    get,
    load,
)
from canon_keeper.templates import build
from canon_keeper_protocol import auth


@pytest.fixture
def blank(tmp_path):
    """An empty campaign to fill."""
    conn = connect(tmp_path / "one.sqlite3")
    migrate(conn)
    repos = Repos(conn)
    campaign = repos.campaigns.ensure_default("Blank")
    yield repos, campaign.id
    conn.close()


def _second(tmp_path, name="two.sqlite3"):
    conn = connect(tmp_path / name)
    migrate(conn)
    repos = Repos(conn)
    campaign = repos.campaigns.ensure_default("Blank")
    return conn, repos, campaign.id


def _snapshot(repos, campaign_id: int) -> dict:
    """Everything that should be identical between two runs."""
    return {
        "entities": [
            (e.kind, e.name, e.summary, json.dumps(e.data, sort_keys=True))
            for e in repos.entities.list(campaign_id)
        ],
        "facts": [
            (f.predicate, f.object) for f in repos.facts.current(campaign_id)
        ],
        "accounts": [
            (a.username, a.role, a.display_name)
            for a in repos.accounts.list(campaign_id)
        ],
        "storyline": repos.settings.get(STORYLINE_SETTING),
    }


# ------------------------------------------------------------------- the files


def test_the_bundled_templates_load():
    assert available(), "no templates ship, which is a packaging failure"


def test_each_one_says_what_it_is():
    for template in available():
        assert template.name
        assert template.summary, f"{template.id} has no line for the chooser"


def test_each_one_knows_where_it_ends():
    """A one-shot that never says where it stops is a campaign."""
    for template in available():
        assert template.ending is not None, f"{template.id} has no ending beat"


def test_only_one_beat_ends_it():
    for template in available():
        endings = [beat for beat in template.storyline if beat.ends_it]
        assert len(endings) == 1, f"{template.id} has {len(endings)} endings"


def test_beat_ids_are_unique():
    for template in available():
        ids = [beat.id for beat in template.storyline]
        assert len(ids) == len(set(ids)), f"{template.id} repeats a beat id"


def test_every_played_character_exists():
    """A login pointing at nothing is a player with no character."""
    for template in available():
        keys = {str(e.get("key") or e.get("name")) for e in template.entities}
        for account in template.accounts:
            plays = account.get("plays")
            if plays:
                assert plays in keys, f"{template.id}: {plays} is not an entity"


def test_every_share_and_parent_points_somewhere():
    for template in available():
        keys = {str(e.get("key") or e.get("name")) for e in template.entities}
        for share in template.shares:
            assert share.get("entity") in keys, f"{template.id}: bad share"
        for entity in template.entities:
            parent = entity.get("parent")
            if parent:
                assert parent in keys, f"{template.id}: bad parent {parent}"


def test_a_broken_file_is_skipped_not_fatal(tmp_path):
    """One bad file must not stop the others being offered."""
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "fine.json").write_text(
        json.dumps({"id": "fine", "name": "Fine", "entities": [{"name": "A"}]}),
        encoding="utf-8",
    )

    found = available(tmp_path)

    assert [t.id for t in found] == ["fine"]


def test_an_empty_template_is_refused(tmp_path):
    path = tmp_path / "hollow.json"
    path.write_text(json.dumps({"id": "hollow", "name": "Hollow"}), encoding="utf-8")

    with pytest.raises(TemplateError):
        load(path)


# --------------------------------------------------------------- the same twice


def test_two_runs_produce_the_same_campaign(blank, tmp_path):
    """The whole feature."""
    repos_one, campaign_one = blank
    template = get("the-last-coach")
    build.populate(repos_one, campaign_one, template)

    conn, repos_two, campaign_two = _second(tmp_path)
    try:
        build.populate(repos_two, campaign_two, template)
        assert _snapshot(repos_one, campaign_one) == _snapshot(repos_two, campaign_two)
    finally:
        conn.close()


def test_the_passwords_are_the_ones_the_template_states(blank):
    """Generated passwords could not be handed out, or tested against."""
    repos, campaign_id = blank
    template = get("test-table")
    build.populate(repos, campaign_id, template)

    account = repos.accounts.by_username(campaign_id, "marco")
    assert auth.derive_verifier("goblin-teeth", account.salt) == account.verifier


def test_players_own_the_characters_they_play(blank):
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("test-table"))

    marco = repos.accounts.by_username(campaign_id, "marco")
    owned = repos.entities.owned_by(marco.id)

    assert [e.name for e in owned] == ["Elara Nightwind"]


def test_places_are_nested(blank):
    """The cellar is in the inn, which is in the town."""
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("test-table"))

    by_name = {e.name: e for e in repos.entities.list(campaign_id)}
    cellar = by_name["The Cellar"]
    inn = by_name["The Stonehill Inn"]

    assert cellar.parent_id == inn.id
    assert inn.parent_id == by_name["Phandalin"].id


def test_a_parent_may_appear_later_in_the_file(tmp_path, blank):
    """Requiring authors to order the file correctly is a trap."""
    repos, campaign_id = blank
    template = Template(
        id="ordering",
        name="Ordering",
        entities=[
            {"key": "room", "kind": "location", "name": "Room", "parent": "house"},
            {"key": "house", "kind": "location", "name": "House"},
        ],
    )
    build.populate(repos, campaign_id, template)

    by_name = {e.name: e for e in repos.entities.list(campaign_id)}
    assert by_name["Room"].parent_id == by_name["House"].id


def test_the_party_starts_knowing_something(blank):
    """A one-shot that shares nothing opens with blank screens."""
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("the-last-coach"))

    assert repos.shares.shared_count(campaign_id) > 0


def test_the_storyline_is_stored_with_nothing_done(blank):
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("the-last-coach"))

    assert len(repos.settings.get(STORYLINE_SETTING)) == 5
    assert repos.settings.get(PROGRESS_SETTING) == []


def test_it_remembers_which_template_it_came_from(blank):
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("test-table"))

    assert repos.settings.get(SOURCE_SETTING) == "test-table"


# ------------------------------------------------------------- starting again


def test_starting_again_puts_it_back(tmp_path):
    """A one-shot run twice should be the same evening twice."""
    path = tmp_path / "oneshot.sqlite3"
    conn = connect(path)
    migrate(conn)
    repos = Repos(conn)
    campaign_id = repos.campaigns.ensure_default("One Shot").id
    build.populate(repos, campaign_id, get("test-table"))
    before = _snapshot(repos, campaign_id)

    # An evening happens.
    from canon_keeper.repo.entities import KIND_NPC, Entity

    repos.entities.create(
        Entity(id=None, campaign_id=campaign_id, kind=KIND_NPC, name="An invention")
    )
    hurt = repos.entities.list(campaign_id)[0]
    hurt.data["hp"] = 1
    repos.entities.update(hurt)
    repos.facts.assert_fact(campaign_id, None, "the party", "burnt the inn down")
    conn.close()

    build.start_again(path)

    conn = connect(path)
    try:
        after = _snapshot(Repos(conn), campaign_id)
        assert after == before
        names = [e[1] for e in after["entities"]]
        assert "An invention" not in names, "last table's leavings should be gone"
    finally:
        conn.close()


def test_starting_again_needs_a_template(tmp_path):
    path = tmp_path / "ordinary.sqlite3"
    conn = connect(path)
    migrate(conn)
    Repos(conn).campaigns.ensure_default("Ordinary")
    conn.close()

    with pytest.raises(TemplateError, match="did not come from a template"):
        build.start_again(path)


def test_keeping_it_makes_it_an_ordinary_campaign(tmp_path):
    """Nothing about the content changes -- there was never anything special."""
    path = tmp_path / "kept.sqlite3"
    conn = connect(path)
    migrate(conn)
    repos = Repos(conn)
    campaign_id = repos.campaigns.ensure_default("Kept").id
    build.populate(repos, campaign_id, get("test-table"))
    before = _snapshot(repos, campaign_id)
    conn.close()

    build.keep(path)

    assert build.source_of(path) == ""
    conn = connect(path)
    try:
        assert _snapshot(Repos(conn), campaign_id) == before
    finally:
        conn.close()

    with pytest.raises(TemplateError):
        build.start_again(path)


def test_an_ordinary_campaign_has_no_source(tmp_path):
    path = tmp_path / "plain.sqlite3"
    conn = connect(path)
    migrate(conn)
    Repos(conn).campaigns.ensure_default("Plain")
    conn.close()

    assert build.source_of(path) == ""


# ------------------------------------------------------------------ the chooser


@pytest.fixture
def chooser(qtbot):
    from canon_keeper.shell.startup import CampaignDialog

    dialog = CampaignDialog()
    qtbot.addWidget(dialog)
    return dialog


def test_the_chooser_offers_the_one_shots(chooser):
    from canon_keeper.shell.startup import TEMPLATE_TAB

    chooser._tabs.setCurrentIndex(TEMPLATE_TAB)
    offered = {
        chooser._template_list.item(row).data(256)
        for row in range(chooser._template_list.count())
    }

    assert {"the-last-coach", "test-combat", "test-table"} <= offered


def test_the_button_says_start_on_that_tab(chooser):
    from PySide6.QtWidgets import QDialogButtonBox

    from canon_keeper.shell.startup import LOCAL_TAB, ONLINE_TAB, TEMPLATE_TAB

    button = chooser._buttons.button(QDialogButtonBox.StandardButton.Open)

    chooser._tabs.setCurrentIndex(LOCAL_TAB)
    assert button.text() == "Open"
    chooser._tabs.setCurrentIndex(ONLINE_TAB)
    assert button.text() == "Join"
    chooser._tabs.setCurrentIndex(TEMPLATE_TAB)
    assert button.text() == "Start"


def test_the_button_starts_a_one_shot_rather_than_opening_a_campaign(
    chooser, monkeypatch
):
    """The tab was added after _accept was written, and fell through to the
    local branch -- so the Open button tried to open whatever was selected on
    another tab."""
    from canon_keeper.shell.startup import TEMPLATE_TAB

    started: list = []
    monkeypatch.setattr(chooser, "_start_template", lambda: started.append(True))
    chooser._tabs.setCurrentIndex(TEMPLATE_TAB)

    chooser._accept()

    assert started == [True]


@pytest.mark.parametrize(
    "which,tab",
    [("_local_list", 0), ("_remote_list", 1), ("_template_list", 2)],
)
def test_every_list_opens_on_a_double_click(chooser, monkeypatch, which, tab):
    """Picking and pressing is one gesture; double-clicking is the other."""
    accepted: list = []
    monkeypatch.setattr(chooser, "_accept", lambda: accepted.append(True))
    chooser._tabs.setCurrentIndex(tab)
    widget = getattr(chooser, which)

    from PySide6.QtWidgets import QListWidgetItem

    item = QListWidgetItem("something")
    widget.addItem(item)
    widget.itemDoubleClicked.emit(item)

    assert accepted == [True], f"{which} ignores a double click"


def test_a_started_one_shot_hands_back_a_path(chooser, monkeypatch, tmp_path):
    """app.main compares these against saved paths; a string is not a Path."""
    from pathlib import Path

    from canon_keeper import campaigns as campaigns_module
    from canon_keeper.shell.startup import TEMPLATE_TAB
    from PySide6.QtWidgets import QInputDialog

    made = campaigns_module.LocalCampaign(tmp_path / "run.sqlite3", "A Run", 0.0)
    monkeypatch.setattr(build, "start", lambda _t, _n: made)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("A Run", True)))
    chooser._tabs.setCurrentIndex(TEMPLATE_TAB)

    chooser._start_template()

    assert isinstance(chooser.launch().path, Path)
    assert chooser.launch().name == "A Run"
