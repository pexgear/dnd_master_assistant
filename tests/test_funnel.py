"""Publishing a session through Tailscale Funnel.

The CLI is stubbed throughout: these tests are about how we drive it and what we
do with what it says, not about Tailscale working. The outputs below are the
shapes the real command produces.
"""

from __future__ import annotations

import json

import pytest

from canon_keeper.net import funnel

SUCCESS_OUTPUT = """Available on the internet:

https://desk.tail1a2b3.ts.net/
|-- proxy http://127.0.0.1:8765

Funnel started and running in the background.
Press Ctrl+C to exit.
"""

NOT_ENABLED_OUTPUT = (
    "Funnel is not enabled on your tailnet.\n"
    "To enable, visit https://login.tailscale.com/admin/settings/keys and add "
    "the funnel attribute."
)


@pytest.fixture
def cli(monkeypatch):
    """Stand in for the tailscale command; records calls, returns queued replies."""
    calls: list[list[str]] = []
    replies: list[tuple[int, str]] = []

    def fake_run(args, timeout=funnel.COMMAND_TIMEOUT):
        calls.append(list(args))
        return replies.pop(0) if replies else (0, "")

    monkeypatch.setattr(funnel, "_run", fake_run)
    monkeypatch.setattr(funnel, "is_installed", lambda: True)
    monkeypatch.setattr(funnel, "executable", lambda: "/usr/bin/tailscale")
    return type("Cli", (), {"calls": calls, "replies": replies})()


# ------------------------------------------------------------------------ urls


def test_https_becomes_wss():
    """Funnel serves HTTPS; a player needs a WebSocket address."""
    assert (
        funnel.to_websocket_url("https://desk.tail1a2b3.ts.net/")
        == "wss://desk.tail1a2b3.ts.net"
    )


def test_an_empty_url_stays_empty():
    assert funnel.to_websocket_url("") == ""


# ----------------------------------------------------------------------- start


def test_starting_publishes_the_port_and_reports_the_address(cli):
    cli.replies.append((0, SUCCESS_OUTPUT))

    result = funnel.start(8765)

    assert result.ok
    assert result.url == "https://desk.tail1a2b3.ts.net"
    assert result.websocket_url == "wss://desk.tail1a2b3.ts.net"
    assert cli.calls == [["funnel", "--bg", "8765"]]


def test_the_tailnet_refusing_is_reported_in_tailscales_own_words(cli):
    """Its message names the setting to change and links to it; ours would not."""
    cli.replies.append((1, NOT_ENABLED_OUTPUT))

    result = funnel.start(8765)

    assert result.ok is False
    assert "not enabled on your tailnet" in result.message
    assert "login.tailscale.com" in result.message


def test_without_tailscale_the_user_is_told_what_to_install(monkeypatch):
    monkeypatch.setattr(funnel, "executable", lambda: None)

    result = funnel.start(8765)

    assert result.ok is False
    assert "tailscale.com/download" in result.message
    assert "only you" in result.message, "players should be told they need nothing"


def test_the_address_is_recovered_when_the_output_does_not_carry_one(cli):
    """Output wording differs between releases, so fall back to status."""
    cli.replies.append((0, "Funnel started."))
    cli.replies.append((0, json.dumps({"Self": {"DNSName": "desk.tail1a2b3.ts.net."}})))

    result = funnel.start(8765)

    assert result.ok
    assert result.websocket_url == "wss://desk.tail1a2b3.ts.net"


def test_success_with_no_address_anywhere_is_a_failure(cli):
    """Reporting success without an address would leave nothing to hand out."""
    cli.replies.append((0, "Funnel started."))
    cli.replies.append((1, "not running"))

    result = funnel.start(8765)

    assert result.ok is False
    assert "did not report a public address" in result.message


def test_a_hung_command_does_not_hang_us(monkeypatch):
    import subprocess

    def explode(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="tailscale", timeout=25)

    monkeypatch.setattr(funnel, "executable", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(subprocess, "run", explode)

    code, output = funnel._run(["funnel", "status"])

    assert code == 124
    assert "did not finish" in output


# ------------------------------------------------------------------------ stop


def test_stopping_turns_the_tunnel_off(cli):
    cli.replies.append((0, ""))

    assert funnel.stop().ok
    assert cli.calls == [["funnel", "--https=443", "off"]]


def test_stopping_falls_back_for_older_releases(cli):
    cli.replies.append((1, "unknown flag: --https"))
    cli.replies.append((0, ""))

    assert funnel.stop().ok
    assert cli.calls == [["funnel", "--https=443", "off"], ["funnel", "reset"]]


def test_stopping_without_tailscale_is_not_an_error(monkeypatch):
    """Nothing of ours is running, so there is nothing to complain about."""
    monkeypatch.setattr(funnel, "executable", lambda: None)
    assert funnel.stop().ok is True


def test_a_failure_to_stop_is_surfaced(cli):
    cli.replies.append((1, "tailscaled is not running"))
    cli.replies.append((1, "tailscaled is not running"))

    result = funnel.stop()

    assert result.ok is False
    assert "tailscaled" in result.message


# ---------------------------------------------------------------------- status


def test_running_is_detected_from_the_status_output(cli):
    cli.replies.append((0, "https://desk.tail1a2b3.ts.net/ proxy 127.0.0.1:8765"))
    assert funnel.is_running() is True


def test_no_serve_config_means_not_running(cli):
    cli.replies.append((0, "No serve config"))
    assert funnel.is_running() is False


def test_machine_url_comes_from_status_json(cli):
    cli.replies.append((0, json.dumps({"Self": {"DNSName": "desk.tail1a2b3.ts.net."}})))
    assert funnel.machine_url() == "https://desk.tail1a2b3.ts.net"


def test_unreadable_status_json_is_survivable(cli):
    cli.replies.append((0, "not json at all"))
    assert funnel.machine_url() == ""


# ------------------------------------------------------------------ the panel


@pytest.fixture
def table(ctx, qtbot):
    from canon_keeper.panels.table.widget import TableWidget

    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    return widget


def test_sharing_needs_a_session_to_share(table):
    """Publishing nothing to the internet is not a useful state."""
    table._funnel_button.setChecked(True)
    table._start_funnel()

    assert table._funnel_button.isChecked() is False
    assert "Go online first" in table._log.toPlainText()


def test_a_published_address_is_shown_and_copyable(table, qtbot):
    table._on_funnel_started(funnel.Result(True, url="https://desk.tail1a2b3.ts.net/"))

    assert table._funnel_url == "wss://desk.tail1a2b3.ts.net"
    assert "wss://desk.tail1a2b3.ts.net" in table._log.toPlainText()
    assert table._funnel_button.text() == "Stop sharing"

    table._copy_invite()
    from PySide6.QtWidgets import QApplication

    assert QApplication.clipboard().text() == "wss://desk.tail1a2b3.ts.net"


def test_a_refusal_leaves_the_button_off(table, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: shown.append(args[-1])
    )
    table._funnel_button.setChecked(True)

    table._on_funnel_started(funnel.Result(False, message=NOT_ENABLED_OUTPUT))

    assert table._funnel_button.isChecked() is False
    assert table._funnel_url == ""
    assert shown and "not enabled on your tailnet" in shown[0]


def test_stopping_clears_the_published_address(table):
    table._on_funnel_started(funnel.Result(True, url="https://desk.tail1a2b3.ts.net/"))
    table._stop_funnel()
    table._on_funnel_stopped(funnel.Result(True))

    assert table._funnel_url == ""
    assert table._funnel_button.text() == "Share on the internet"
