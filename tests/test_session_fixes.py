"""Three things that were quietly wrong at the table.

A refusal that left the rejected change on screen. A chat log where the game was
buried under people arriving and leaving. And updates landing in panels nobody
was looking at, with nothing to say so.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDockWidget, QMainWindow

from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper.panels.table.widget import TableWidget
from canon_keeper.repo.entities import KIND_PC, Entity
from canon_keeper.shell.attention import TINT_ALPHA, Attention
from canon_keeper_protocol import MessageType, SystemKind


# --------------------------------------------------------------- the refusal


@pytest.fixture
def table_with_player(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Refusals")
    elara = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Elara",
            summary="A wizard.",
        )
    )
    marco = repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", display_name="Marco",
        character_entity_id=elara.id,
    )
    repos.entities.set_owner(elara.id, marco.id)

    server = SessionServer(repos, campaign.id, "Refusals")
    assert server.start(0, announce=False)
    yield server, repos.entities.get(elara.id)
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def test_a_refusal_tells_the_player_which_character(qtbot, table_with_player):
    """Without the id, a panel cannot know what to put back."""
    server, elara = table_with_player
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.system, timeout=5000):
            player.send_edit(elara.id, {"summary": "A wizard, and secretly a god."})

        waiting = server.proposals
        assert waiting, "the request should be queued"

        with qtbot.waitSignal(player.edit_refused, timeout=5000) as blocker:
            server.decide(waiting[0]["id"], False, "not yet")

        entity_id, reason = blocker.args
        assert entity_id == elara.id
        assert reason == "not yet"
    finally:
        player.leave()


def test_a_refusal_also_sends_back_what_is_true(qtbot, table_with_player):
    """The reason alone leaves the rejected change sitting on their screen."""
    server, elara = table_with_player
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.system, timeout=5000):
            player.send_edit(elara.id, {"summary": "A wizard, and secretly a god."})
        waiting = server.proposals

        with qtbot.waitSignal(player.state.changed, timeout=5000):
            server.decide(waiting[0]["id"], False, "not yet")

        held = player.state.get(elara.id)
        assert held["summary"] == "A wizard.", (
            "the player should be holding what the DM actually allows, not what "
            "they asked for"
        )
    finally:
        player.leave()


def test_an_approval_does_not_look_like_a_refusal(qtbot, table_with_player):
    server, elara = table_with_player
    player = _join(qtbot, server, "marco", "goblin-teeth")
    refusals: list = []
    player.edit_refused.connect(lambda *args: refusals.append(args))
    try:
        with qtbot.waitSignal(player.system, timeout=5000):
            player.send_edit(elara.id, {"summary": "A wizard, and a good one."})
        waiting = server.proposals

        with qtbot.waitSignal(player.state.changed, timeout=5000):
            server.decide(waiting[0]["id"], True)

        assert refusals == []
        assert player.state.get(elara.id)["summary"] == "A wizard, and a good one."
    finally:
        player.leave()


# ------------------------------------------------------------- the chat filter


@pytest.fixture
def table(ctx, qtbot) -> TableWidget:
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    return widget


def test_joins_and_leaves_are_hidden_by_default(table):
    table._append_system("Marco joined", SystemKind.CHATTER.value)
    assert "Marco joined" not in table._log.toPlainText()


def test_but_they_are_kept_and_can_be_shown(table):
    table._append_system("Marco joined", SystemKind.CHATTER.value)

    table._show_chatter.setChecked(True)

    assert "Marco joined" in table._log.toPlainText()


def test_and_hidden_again(table):
    table._append_system("Marco joined", SystemKind.CHATTER.value)
    table._show_chatter.setChecked(True)
    table._show_chatter.setChecked(False)

    assert "Marco joined" not in table._log.toPlainText()


def test_a_notice_is_never_hidden(table):
    """The whole point of the distinction: a refusal is not housekeeping."""
    table._append_system("Your DM said no to a level change", SystemKind.NOTICE.value)
    assert "said no" in table._log.toPlainText()


def test_nor_is_anything_said_at_the_table(table):
    table._append("said", "Elara: I check the door.")
    table._append("roll", "Elara rolled 17")
    table._append("error", "The agent stopped.")

    shown = table._log.toPlainText()
    assert "I check the door." in shown
    assert "rolled 17" in shown
    assert "The agent stopped." in shown


def test_the_filter_keeps_the_order(table):
    table._append("said", "Elara: one")
    table._append_system("Marco joined", SystemKind.CHATTER.value)
    table._append("said", "Elara: two")

    table._show_chatter.setChecked(True)
    shown = table._log.toPlainText()

    assert shown.index("one") < shown.index("Marco joined") < shown.index("two")


def test_replayed_history_is_filtered_the_same_way(table):
    """Old joins are the same housekeeping as live ones."""
    table._on_history(
        [
            {"kind": "system", "text": "Marco joined", "at": 0},
            {"kind": "said", "speaker": "Elara", "text": "I check the door.", "at": 1},
        ]
    )

    assert "Marco joined" not in table._log.toPlainText()
    assert "I check the door." in table._log.toPlainText()


# ------------------------------------------------------------ panel attention


@pytest.fixture
def flagged(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    attention = Attention(window)
    dock = QDockWidget("Characters", window)
    dock.setObjectName("characters")
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
    attention.watch("characters", dock)
    return attention, dock, window


def test_a_hidden_panel_is_tinted_when_something_arrives(flagged):
    attention, dock, _window = flagged
    dock.hide()

    attention.flag("characters")

    assert attention.is_waiting("characters")
    assert "background" in dock.styleSheet()


def test_a_panel_you_are_looking_at_is_not(flagged, qtbot):
    """Lighting up what is already on screen tells you nothing."""
    attention, dock, window = flagged
    window.show()
    qtbot.waitExposed(window)

    attention.flag("characters")

    assert not attention.is_waiting("characters")
    assert dock.styleSheet() == ""


def test_seeing_it_clears_the_flag(flagged, qtbot):
    attention, dock, window = flagged
    window.show()
    qtbot.waitExposed(window)
    dock.hide()
    attention.flag("characters")
    assert attention.is_waiting("characters")

    dock.show()  # visibilityChanged fires

    assert not attention.is_waiting("characters")


def test_the_tint_follows_the_theme(flagged):
    attention, dock, _window = flagged
    dock.hide()
    attention.flag("characters")

    attention.set_colour(QColor(200, 30, 30))

    assert "200, 30, 30" in dock.styleSheet()


def test_flagging_an_unknown_panel_is_harmless(flagged):
    attention, _dock, _window = flagged
    attention.flag("a-panel-that-is-not-installed")  # must not raise


def test_clearing_something_not_waiting_is_harmless(flagged):
    attention, _dock, _window = flagged
    attention.clear("characters")


def test_the_tint_is_visible_but_not_a_shout():
    assert 40 <= TINT_ALPHA <= 140, (
        "a title bar the reader cannot ignore is worse than no highlight at all"
    )


def test_the_message_types_exist():
    assert MessageType.REFUSED == "refused"
    assert SystemKind.CHATTER != SystemKind.NOTICE
