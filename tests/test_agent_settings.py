"""The dialog where the agent's key and model are set.

The thing being fixed here is not the missing dialog, it is the missing way
*back*. A key typed once into a one-line prompt and saved forever is fine until
you fat-finger it, at which point the only symptom is an agent that never
answers and no screen anywhere that will let you correct it.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox

from canon_keeper import agent_runner, credentials
from canon_keeper.panels.table.agent_settings import (
    MODEL_SETTING,
    MODELS,
    AgentSettingsDialog,
)


@pytest.fixture
def store(monkeypatch):
    """A credential store that works, without touching the real one."""
    kept: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(credentials, "is_available", lambda: True)
    monkeypatch.setattr(
        credentials,
        "save",
        lambda url, user, password: kept.__setitem__((url, user), password) or True,
    )
    monkeypatch.setattr(credentials, "load", lambda url, user: kept.get((url, user)))
    monkeypatch.setattr(
        credentials, "forget", lambda url, user: kept.pop((url, user), None)
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return kept


@pytest.fixture
def dialog(ctx, qtbot, store):
    widget = AgentSettingsDialog(ctx)
    qtbot.addWidget(widget)
    return widget


# --------------------------------------------------------------------- the key


def test_the_key_is_masked(dialog):
    assert dialog._key.echoMode() == QLineEdit.EchoMode.Password


def test_it_can_be_revealed(dialog):
    """You cannot check a pasted key you cannot see."""
    dialog._show.setChecked(True)
    assert dialog._key.echoMode() == QLineEdit.EchoMode.Normal


def test_and_hidden_again(dialog):
    dialog._show.setChecked(True)
    dialog._show.setChecked(False)
    assert dialog._key.echoMode() == QLineEdit.EchoMode.Password


def test_an_existing_key_is_shown_for_editing(ctx, qtbot, store):
    """The whole point: a mistyped key must be correctable."""
    agent_runner.remember_api_key("sk-ant-the-old-one")
    widget = AgentSettingsDialog(ctx)
    qtbot.addWidget(widget)

    assert widget.key == "sk-ant-the-old-one"


def test_saving_replaces_it(ctx, qtbot, store):
    agent_runner.remember_api_key("sk-ant-typo")
    widget = AgentSettingsDialog(ctx)
    qtbot.addWidget(widget)

    widget._key.setText("sk-ant-corrected")
    widget._on_save()

    assert agent_runner.api_key() == "sk-ant-corrected"


def test_forgetting_clears_it(ctx, qtbot, store):
    agent_runner.remember_api_key("sk-ant-goodbye")
    widget = AgentSettingsDialog(ctx)
    qtbot.addWidget(widget)

    widget._on_forget()

    assert agent_runner.api_key() == ""
    assert widget.key == ""


def test_a_key_that_does_not_look_like_one_is_questioned(dialog, monkeypatch):
    """The usual mistake is pasting the key's *name* from the console."""
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: asked.append(args[1])
        or QMessageBox.StandardButton.Cancel,
    )
    dialog._key.setText("my-dnd-key")

    dialog._on_save()

    assert asked, "saving something that is not a key should ask first"
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_but_you_can_insist(dialog, monkeypatch, store):
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save
    )
    dialog._key.setText("definitely-my-key")

    dialog._on_save()

    assert agent_runner.api_key() == "definitely-my-key"


def test_a_normal_key_is_not_questioned(dialog, monkeypatch, store):
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: pytest.fail("a well-formed key should just save"),
    )
    dialog._key.setText("sk-ant-perfectly-fine")
    dialog._on_save()

    assert agent_runner.api_key() == "sk-ant-perfectly-fine"


# ------------------------------------------------------------------- the model


def test_the_model_defaults_to_the_first_offered(dialog):
    assert dialog.model == MODELS[0][0]


def test_choosing_a_model_is_remembered_on_the_campaign(ctx, dialog):
    dialog._model.setCurrentIndex(1)
    dialog._key.setText("sk-ant-fine")

    dialog._on_save()

    assert ctx.repos.settings.get(MODEL_SETTING) == MODELS[1][0]


def test_a_remembered_model_comes_back(ctx, qtbot, store):
    ctx.repos.settings.set(MODEL_SETTING, MODELS[2][0])
    widget = AgentSettingsDialog(ctx)
    qtbot.addWidget(widget)

    assert widget.model == MODELS[2][0]


def test_every_offered_model_has_a_readable_label():
    for value, label in MODELS:
        assert value and label
        assert value not in label, "the label is for a person, not a machine"


# ------------------------------------------------------------ saying where it goes


def test_it_says_where_the_key_goes(dialog):
    text = dialog._where_it_goes()
    assert "credential store" in text
    assert "campaign file" in text, "people copy campaigns about; say what travels"


def test_without_a_store_it_says_so_and_offers_the_environment(monkeypatch):
    monkeypatch.setattr(credentials, "is_available", lambda: False)
    text = AgentSettingsDialog._where_it_goes()

    assert "cannot be kept" in text
    assert "ANTHROPIC_API_KEY" in text


def test_a_key_typed_on_a_machine_with_no_store_is_still_usable(ctx, qtbot, monkeypatch):
    """It cannot be saved, but it must still start this session's agent."""
    monkeypatch.setattr(credentials, "is_available", lambda: False)
    monkeypatch.setattr(credentials, "save", lambda *_a: False)
    monkeypatch.setattr(credentials, "load", lambda *_a: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    widget = AgentSettingsDialog(ctx)
    qtbot.addWidget(widget)
    widget._key.setText("sk-ant-for-this-run")

    assert widget.key == "sk-ant-for-this-run", (
        "the caller reads the key off the dialog, not back out of a store that "
        "did not keep it"
    )


# ------------------------------------------------------------- the workspace id
#
# Some keys are identity-linked and are refused outright without the id of the
# workspace they belong to. Nothing can detect that in advance -- the API says
# so, in a message the DM now sees -- so there has to be somewhere to put the
# answer.


def test_a_workspace_id_can_be_set_and_comes_back(ctx, qtbot, store):
    widget = AgentSettingsDialog(ctx)
    qtbot.addWidget(widget)
    widget._key.setText("sk-ant-fine")
    widget._workspace.setText("wrkspc_01abc")

    widget._on_save()

    assert agent_runner.workspace_id() == "wrkspc_01abc"

    again = AgentSettingsDialog(ctx)
    qtbot.addWidget(again)
    assert again.workspace == "wrkspc_01abc"


def test_clearing_it_actually_clears_it(ctx, qtbot, store):
    """Saving only non-empty values would make a wrong id impossible to remove."""
    agent_runner.remember_workspace_id("wrkspc_wrong")

    widget = AgentSettingsDialog(ctx)
    qtbot.addWidget(widget)
    widget._key.setText("sk-ant-fine")
    widget._workspace.clear()
    widget._on_save()

    assert agent_runner.workspace_id() == ""


def test_forgetting_the_key_forgets_the_workspace_too(ctx, qtbot, store):
    agent_runner.remember_api_key("sk-ant-bye")
    agent_runner.remember_workspace_id("wrkspc_bye")

    widget = AgentSettingsDialog(ctx)
    qtbot.addWidget(widget)
    widget._on_forget()

    assert agent_runner.api_key() == ""
    assert agent_runner.workspace_id() == ""


def test_the_environment_wins(monkeypatch, store):
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_from_env")
    agent_runner.remember_workspace_id("wrkspc_stored")
    assert agent_runner.workspace_id() == "wrkspc_from_env"


def test_no_workspace_is_an_empty_string(monkeypatch):
    from canon_keeper import credentials as creds

    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    monkeypatch.setattr(creds, "load", lambda *_a: None)
    assert agent_runner.workspace_id() == ""


def test_it_reaches_the_agent_on_the_command_line(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        agent_runner.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(command=command) or _NoOutput(),
    )
    monkeypatch.setattr(agent_runner, "find_executable", lambda: ["canonkeeper-agent"])

    agent_runner.start("ws://127.0.0.1:8766", "autopilot", "pw", workspace="wrkspc_01")

    assert "--workspace" in captured["command"]
    assert "wrkspc_01" in captured["command"]


def test_no_workspace_means_no_flag(monkeypatch):
    """An empty --workspace would be argued about by the parser for no reason."""
    captured = {}
    monkeypatch.setattr(
        agent_runner.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(command=command) or _NoOutput(),
    )
    monkeypatch.setattr(agent_runner, "find_executable", lambda: ["canonkeeper-agent"])

    agent_runner.start("ws://127.0.0.1:8766", "autopilot", "pw")

    assert "--workspace" not in captured["command"]


class _NoOutput:
    """A started process with nothing to read."""

    returncode = 0
    stdout = None

    def poll(self):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_the_brain_sends_the_header_when_it_has_one(monkeypatch):
    """The whole point: the API refuses some keys without it."""
    from canon_keeper_dm_agent.brain import Brain

    captured = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fine")
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)

    Brain(workspace="wrkspc_01abc")._ensure_client()

    assert captured["default_headers"] == {"anthropic-workspace-id": "wrkspc_01abc"}


def test_and_no_header_when_it_does_not(monkeypatch):
    from canon_keeper_dm_agent.brain import Brain

    captured = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fine")
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)

    Brain()._ensure_client()

    assert captured["default_headers"] is None
