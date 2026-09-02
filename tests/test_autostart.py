"""Saved logins and opening a campaign without being asked."""

from __future__ import annotations

from pathlib import Path

import pytest

from canon_keeper import campaigns, config, credentials
from canon_keeper.shell.startup import CampaignDialog, Launch


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CANONKEEPER_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def vault(monkeypatch):
    """An in-memory stand-in for the OS credential store."""
    store: dict[tuple[str, str], str] = {}

    class _FakeKeyring:
        @staticmethod
        def set_password(service, account, password):
            store[(service, account)] = password

        @staticmethod
        def get_password(service, account):
            return store.get((service, account))

        @staticmethod
        def delete_password(service, account):
            store.pop((service, account), None)

    monkeypatch.setattr(credentials, "_keyring", lambda: _FakeKeyring)
    monkeypatch.setattr(credentials, "is_available", lambda: True)
    return store


# ------------------------------------------------------------------- storage


def test_a_password_round_trips(vault):
    assert credentials.save("ws://host:8765", "marco", "goblin-teeth") is True
    assert credentials.load("ws://host:8765", "marco") == "goblin-teeth"


def test_logins_for_different_sessions_do_not_collide(vault):
    credentials.save("ws://a:8765", "marco", "one")
    credentials.save("ws://b:8765", "marco", "two")

    assert credentials.load("ws://a:8765", "marco") == "one"
    assert credentials.load("ws://b:8765", "marco") == "two"


def test_usernames_are_matched_case_insensitively(vault):
    credentials.save("ws://host:8765", "Marco", "goblin-teeth")
    assert credentials.load("ws://host:8765", "marco") == "goblin-teeth"


def test_forgetting_removes_the_password(vault):
    credentials.save("ws://host:8765", "marco", "goblin-teeth")
    credentials.forget("ws://host:8765", "marco")
    assert credentials.load("ws://host:8765", "marco") is None


def test_nothing_is_saved_without_a_credential_store(monkeypatch):
    monkeypatch.setattr(credentials, "is_available", lambda: False)
    assert credentials.save("ws://host:8765", "marco", "goblin-teeth") is False
    assert credentials.load("ws://host:8765", "marco") is None


def test_a_broken_keyring_does_not_raise(monkeypatch):
    class _Exploding:
        @staticmethod
        def set_password(*_a):
            raise RuntimeError("the keychain is locked")

        @staticmethod
        def get_password(*_a):
            raise RuntimeError("the keychain is locked")

    monkeypatch.setattr(credentials, "_keyring", lambda: _Exploding)
    monkeypatch.setattr(credentials, "is_available", lambda: True)

    assert credentials.save("ws://host:8765", "marco", "pw") is False
    assert credentials.load("ws://host:8765", "marco") is None


def test_passwords_are_not_written_to_our_own_files(vault):
    credentials.save("ws://host:8765", "marco", "goblin-teeth")
    campaigns.remember_remote("ws://host:8765", "Game", "marco")
    campaigns.set_autostart(
        campaigns.Autostart(kind="remote", url="ws://host:8765", username="marco")
    )

    for path in config.data_dir().rglob("*.json"):
        assert "goblin-teeth" not in path.read_text("utf-8"), f"password leaked into {path}"


# ----------------------------------------------------------------- autostart


def test_no_autostart_by_default():
    assert campaigns.get_autostart() is None


def test_a_local_campaign_can_be_set_to_open_automatically():
    campaign = campaigns.create_local("Phandalin")
    campaigns.set_autostart(
        campaigns.Autostart(kind="local", path=str(campaign.path), name="Phandalin")
    )

    entry = campaigns.get_autostart()
    assert entry is not None
    assert entry.kind == "local"
    assert entry.path == str(campaign.path)


def test_a_deleted_campaign_does_not_lock_you_out():
    """Otherwise the app would try to open a file that is gone, every time."""
    campaign = campaigns.create_local("Doomed")
    campaigns.set_autostart(campaigns.Autostart(kind="local", path=str(campaign.path)))
    campaigns.delete_local(campaign.path)

    assert campaigns.get_autostart() is None
    assert not (config.data_dir() / campaigns.AUTOSTART_FILE).exists()


def test_clearing_autostart_brings_the_chooser_back():
    campaigns.set_autostart(campaigns.Autostart(kind="remote", url="ws://host:8765"))
    campaigns.clear_autostart()
    assert campaigns.get_autostart() is None


def test_a_corrupt_autostart_file_is_ignored():
    (config.data_dir() / campaigns.AUTOSTART_FILE).write_text("{ nope", "utf-8")
    assert campaigns.get_autostart() is None


# ------------------------------------------------------- resolving on launch


def _args(**overrides):
    class _Args:
        db = None
        player = False
        choose = False

    args = _Args()
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_a_remote_autostart_opens_with_the_saved_password(vault, caplog):
    from canon_keeper.app import _resolve_launch

    credentials.save("ws://host:8765", "marco", "goblin-teeth")
    campaigns.set_autostart(
        campaigns.Autostart(
            kind="remote", url="ws://host:8765", username="marco", name="Our campaign"
        )
    )

    launch = _resolve_launch(_args(), __import__("logging").getLogger("t"))

    assert launch is not None
    assert launch.role == "player"
    assert launch.password == "goblin-teeth"


def test_without_a_saved_password_the_chooser_is_shown(vault, monkeypatch):
    """Better to ask than to fail a login the user never saw."""
    from canon_keeper import app as app_module

    campaigns.set_autostart(
        campaigns.Autostart(kind="remote", url="ws://host:8765", username="marco")
    )
    asked = []
    monkeypatch.setattr(
        app_module, "choose_campaign", lambda start_online=False: asked.append(1)
    )

    app_module._resolve_launch(_args(), __import__("logging").getLogger("t"))
    assert asked == [1]


def test_the_choose_flag_overrides_autostart(monkeypatch):
    from canon_keeper import app as app_module

    campaign = campaigns.create_local("Phandalin")
    campaigns.set_autostart(campaigns.Autostart(kind="local", path=str(campaign.path)))
    asked = []
    monkeypatch.setattr(
        app_module, "choose_campaign", lambda start_online=False: asked.append(1)
    )

    app_module._resolve_launch(_args(choose=True), __import__("logging").getLogger("t"))
    assert asked == [1]


def test_switching_campaign_forces_the_chooser(monkeypatch):
    from canon_keeper import app as app_module

    campaign = campaigns.create_local("Phandalin")
    campaigns.set_autostart(campaigns.Autostart(kind="local", path=str(campaign.path)))
    asked = []
    monkeypatch.setattr(
        app_module, "choose_campaign", lambda start_online=False: asked.append(1)
    )

    app_module._resolve_launch(
        _args(), __import__("logging").getLogger("t"), force_chooser=True
    )
    assert asked == [1]


# -------------------------------------------------------- applying the boxes


def test_a_login_that_worked_is_saved_without_being_asked(vault):
    """Joining your own weekly game is not a question with two answers.

    This only ever runs after the host has accepted the login, so what reaches
    the credential store is known to work rather than merely typed.
    """
    from canon_keeper.app import _remember_choices

    _remember_choices(
        Launch(
            kind="remote",
            url="ws://host:8765",
            username="marco",
            password="goblin-teeth",
        )
    )
    assert credentials.load("ws://host:8765", "marco") == "goblin-teeth"


def test_a_changed_password_replaces_the_saved_one(vault):
    from canon_keeper.app import _remember_choices

    credentials.save("ws://host:8765", "marco", "the-old-one")
    _remember_choices(
        Launch(
            kind="remote",
            url="ws://host:8765",
            username="marco",
            password="the-new-one",
        )
    )
    assert credentials.load("ws://host:8765", "marco") == "the-new-one"


def test_unticking_autostart_clears_it_for_that_campaign(vault):
    from canon_keeper.app import _remember_choices

    campaign = campaigns.create_local("Phandalin")
    campaigns.set_autostart(campaigns.Autostart(kind="local", path=str(campaign.path)))

    _remember_choices(Launch(kind="local", path=campaign.path, autostart=False))
    assert campaigns.get_autostart() is None


def test_opening_another_campaign_does_not_clear_someone_elses_autostart(vault):
    """Unticking is only meaningful for the campaign it was set on."""
    from canon_keeper.app import _remember_choices

    first = campaigns.create_local("Phandalin")
    second = campaigns.create_local("Neverwinter")
    campaigns.set_autostart(campaigns.Autostart(kind="local", path=str(first.path)))

    _remember_choices(Launch(kind="local", path=second.path, autostart=False))

    entry = campaigns.get_autostart()
    assert entry is not None and entry.path == str(first.path)


def test_joining_records_the_server_for_the_list(vault):
    from canon_keeper.app import _remember_choices

    _remember_choices(
        Launch(
            kind="remote",
            url="ws://host:8765",
            username="marco",
            password="pw",
            name="Our campaign",
        )
    )
    assert [s.url for s in campaigns.list_remote()] == ["ws://host:8765"]


# ------------------------------------------------------------------ chooser


def test_the_chooser_asks_nothing_about_remembering(qtbot, vault):
    """The box is gone. A login that works is kept, and that is the whole rule."""
    dialog = CampaignDialog(start_online=True)
    qtbot.addWidget(dialog)

    assert not hasattr(dialog, "_remember")


def test_a_saved_password_is_filled_in_when_you_pick_the_session(qtbot, vault):
    credentials.save("ws://host:8765", "marco", "goblin-teeth")
    campaigns.remember_remote("ws://host:8765", "Our campaign", "marco")

    dialog = CampaignDialog(start_online=True)
    qtbot.addWidget(dialog)
    dialog._remote_list.setCurrentRow(0)

    assert dialog._username.text() == "marco"
    assert dialog._password.text() == "goblin-teeth"


def test_a_local_campaign_can_be_marked_to_open_automatically(qtbot):
    campaign = campaigns.create_local("Phandalin")
    dialog = CampaignDialog()
    qtbot.addWidget(dialog)
    dialog._local_list.setCurrentRow(0)
    dialog._autostart_local.setChecked(True)

    dialog._accept()
    launch = dialog.launch()

    assert launch.autostart is True
    assert launch.path == campaign.path


def test_the_checkbox_shows_the_campaign_that_opens_automatically(qtbot):
    campaign = campaigns.create_local("Phandalin")
    campaigns.set_autostart(campaigns.Autostart(kind="local", path=str(campaign.path)))

    dialog = CampaignDialog()
    qtbot.addWidget(dialog)
    dialog._local_list.setCurrentRow(0)

    assert dialog._autostart_local.isChecked() is True


def test_no_credential_store_is_not_an_error(qtbot, monkeypatch, vault):
    """Nothing is stored and nothing complains; you type it each time."""
    from canon_keeper.app import _remember_choices

    monkeypatch.setattr(credentials, "is_available", lambda: False)
    dialog = CampaignDialog(start_online=True)
    qtbot.addWidget(dialog)

    _remember_choices(
        Launch(
            kind="remote", url="ws://host:8765", username="marco", password="pw"
        )
    )  # must not raise
