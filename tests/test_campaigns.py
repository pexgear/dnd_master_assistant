"""Finding campaigns, and the chooser that gates the app."""

from __future__ import annotations

from pathlib import Path

import pytest

from canon_keeper import campaigns, config
from canon_keeper.shell.startup import LOCAL_TAB, ONLINE_TAB, CampaignDialog, Launch


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Keep the tests off the real campaigns directory."""
    monkeypatch.setenv("CANONKEEPER_DATA_DIR", str(tmp_path))
    return tmp_path


# ----------------------------------------------------------------------- local


def test_a_new_campaign_appears_in_the_list():
    assert campaigns.list_local() == []

    campaigns.create_local("Lost Mine of Phandelver")
    found = campaigns.list_local()

    assert len(found) == 1
    assert found[0].name == "Lost Mine of Phandelver"
    assert found[0].path.suffix == ".sqlite3"


def test_the_filename_is_derived_from_the_name():
    campaign = campaigns.create_local("Lost Mine of Phandelver")
    assert campaign.path.name == "lost-mine-of-phandelver.sqlite3"


def test_two_campaigns_with_one_name_do_not_collide():
    first = campaigns.create_local("Our campaign")
    second = campaigns.create_local("Our campaign")

    assert first.path != second.path
    assert len(campaigns.list_local()) == 2


def test_awkward_names_still_produce_a_usable_filename():
    campaign = campaigns.create_local("  ../../etc/passwd?!  ")
    assert campaign.path.parent == config.campaigns_dir()
    assert "/" not in campaign.path.name and "\\" not in campaign.path.name


def test_a_created_campaign_is_migrated_and_named():
    campaign = campaigns.create_local("Phandalin")
    assert campaigns.campaign_name_of(campaign.path) == "Phandalin"


def test_a_file_that_is_not_a_campaign_is_listed_by_its_stem():
    """A stray file must not crash the chooser."""
    junk = config.campaigns_dir() / "notacampaign.sqlite3"
    junk.write_bytes(b"this is not a database")

    found = {c.label for c in campaigns.list_local()}
    assert "notacampaign" in found


def test_deleting_removes_the_file_and_its_sidecars():
    campaign = campaigns.create_local("Doomed")
    Path(str(campaign.path) + "-wal").write_bytes(b"")

    campaigns.delete_local(campaign.path)

    assert not campaign.path.exists()
    assert not Path(str(campaign.path) + "-wal").exists()
    assert campaigns.list_local() == []


def test_the_profile_is_not_a_campaign():
    """Settings and layouts must not show up as something to open."""
    config.profile_db_path().write_bytes(b"")
    assert config.profile_db_path().parent != config.campaigns_dir()
    assert campaigns.list_local() == []


# ---------------------------------------------------------------------- remote


def test_a_joined_server_is_remembered():
    campaigns.remember_remote("ws://192.168.1.10:8765", "Our campaign", "marco")
    [remembered] = campaigns.list_remote()

    assert remembered.url == "ws://192.168.1.10:8765"
    assert remembered.username == "marco"


def test_the_password_is_never_remembered():
    """A stolen laptop should not also be a stolen campaign."""
    campaigns.remember_remote("ws://host:8765", "Game", "marco")
    stored = (config.data_dir() / campaigns.RECENT_SERVERS_FILE).read_text("utf-8")
    assert "password" not in stored.lower()


def test_rejoining_moves_a_server_to_the_top():
    campaigns.remember_remote("ws://a:8765", "A", "marco")
    campaigns.remember_remote("ws://b:8765", "B", "marco")
    campaigns.remember_remote("ws://a:8765", "A", "marco")

    assert [s.url for s in campaigns.list_remote()] == ["ws://a:8765", "ws://b:8765"]
    assert len(campaigns.list_remote()) == 2


def test_forgetting_a_server_removes_it():
    campaigns.remember_remote("ws://a:8765")
    campaigns.forget_remote("ws://a:8765")
    assert campaigns.list_remote() == []


def test_a_corrupt_server_list_is_survivable():
    (config.data_dir() / campaigns.RECENT_SERVERS_FILE).write_text("{ nope", "utf-8")
    assert campaigns.list_remote() == []


# --------------------------------------------------------------------- chooser


def test_the_chooser_lists_local_campaigns(qtbot):
    campaigns.create_local("Phandalin")
    dialog = CampaignDialog()
    qtbot.addWidget(dialog)

    assert dialog._local_list.count() == 1
    assert "Phandalin" in dialog._local_list.item(0).text()


def test_opening_a_local_campaign_makes_you_its_dm(qtbot):
    campaign = campaigns.create_local("Phandalin")
    dialog = CampaignDialog()
    qtbot.addWidget(dialog)
    dialog._local_list.setCurrentRow(0)

    dialog._accept()
    launch = dialog.launch()

    assert launch is not None
    assert launch.kind == "local"
    assert launch.role == "dm"
    assert launch.path == campaign.path


def test_joining_a_session_makes_you_a_player(qtbot):
    dialog = CampaignDialog(start_online=True)
    qtbot.addWidget(dialog)
    dialog._url.setText("ws://192.168.1.10:8765")
    dialog._username.setText("marco")
    dialog._password.setText("goblin-teeth")

    dialog._accept()
    launch = dialog.launch()

    assert launch is not None
    assert launch.role == "player"
    assert launch.username == "marco"
    assert launch.password == "goblin-teeth"


def test_joining_without_credentials_is_refused(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    dialog = CampaignDialog(start_online=True)
    qtbot.addWidget(dialog)
    dialog._url.setText("ws://192.168.1.10:8765")

    dialog._accept()

    assert dialog.launch() is None, "joined with no username or password"


def test_opening_with_nothing_selected_is_refused(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    dialog = CampaignDialog()
    qtbot.addWidget(dialog)

    dialog._accept()

    assert dialog.launch() is None


def test_the_button_says_what_it_will_do(qtbot):
    from PySide6.QtWidgets import QDialogButtonBox

    dialog = CampaignDialog()
    qtbot.addWidget(dialog)
    button = dialog._buttons.button(QDialogButtonBox.StandardButton.Open)

    dialog._tabs.setCurrentIndex(LOCAL_TAB)
    assert button.text() == "Open"

    dialog._tabs.setCurrentIndex(ONLINE_TAB)
    assert button.text() == "Join"


def test_picking_a_remembered_server_fills_in_the_username(qtbot):
    campaigns.remember_remote("ws://192.168.1.10:8765", "Our campaign", "marco")
    dialog = CampaignDialog(start_online=True)
    qtbot.addWidget(dialog)

    dialog._remote_list.setCurrentRow(0)

    assert dialog._url.text() == "ws://192.168.1.10:8765"
    assert dialog._username.text() == "marco"
    assert dialog._password.text() == "", "the password is never restored"


def test_launch_roles_are_decided_by_where_the_campaign_lives():
    assert Launch(kind="local", path=Path("x.sqlite3")).role == "dm"
    assert Launch(kind="remote", url="ws://host:8765").role == "player"
