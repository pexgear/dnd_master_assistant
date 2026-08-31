"""Host or join a session, then chat and roll dice in it.

Hosting starts a server and then connects to it over loopback like anybody else,
so the DM's own messages travel the same path a player's do.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import (
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from canon_keeper import agent_runner, campaigns, credentials
from canon_keeper.audio.dictation import Dictation
from canon_keeper.net import discovery, funnel
from canon_keeper.net.client import SessionClient
from canon_keeper_protocol.messages import Member, Role, SystemKind
from canon_keeper.net.server import DEFAULT_PORT, SessionServer
from canon_keeper.panels.table.agent_settings import (
    MODEL_SETTING,
    AgentSettingsDialog,
)
from canon_keeper.panels.table.approvals import ApprovalsDialog
from canon_keeper.panels.table.dialogs import AccountsDialog, HostDialog, JoinDialog
from canon_keeper.plugin import AppContext

class _FunnelSignals(QObject):
    done = Signal(object)  # funnel.Result


class _FunnelTask(QRunnable):
    """Runs one tailscale command off the GUI thread.

    The CLI usually answers in under a second, but the first run of Funnel on a
    tailnet can sit there while certificates are provisioned -- long enough to
    look like a hang if it were done inline.
    """

    def __init__(self, action, *args) -> None:
        super().__init__()
        self.signals = _FunnelSignals()
        self._action = action
        self._args = args

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        try:
            result = self._action(*self._args)
        except Exception as exc:  # noqa: BLE001 - report, never take the app down
            result = funnel.Result(False, message=str(exc))
        self.signals.done.emit(result)


QUICK_DICE = ("d20", "d12", "d10", "d8", "d6", "d4")


class TableWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._server: SessionServer | None = None
        #: Credentials to save once the host actually accepts them. Saving
        #: before that would store a password we have no reason to believe.
        self._pending_credentials: tuple[str, str, str] | None = None
        self._funnel_url = ""
        #: Set while relaying a player's edit to the DM's own panels, so the
        #: refresh does not look like a change *by* the DM.
        self._relaying_player_edit = False
        #: The agent we started, if we started one. An agent someone else
        #: is running is not ours to stop.
        self._agent_process = None
        #: Who is composing right now, by label.
        self._busy: set[str] = set()
        #: Every line, shown or not, so the filter can be turned back on.
        self._entries: list[tuple[str, str, float]] = []
        # An agent that dies a millisecond after starting used to do so in
        # silence, leaving the button on and the table waiting for a machine
        # that was not there.
        self._agent_watchdog = QTimer(self)
        self._agent_watchdog.setInterval(1000)
        self._agent_watchdog.timeout.connect(self._check_agent)

        # Speaking instead of typing. The text lands in the box rather than
        # being sent, because a transcription is a first draft.
        self._dictation = Dictation(self)
        self._dictation.text_ready.connect(self._on_dictated)
        self._dictation.status.connect(self._on_dictation_status)
        self._dictation.failed.connect(lambda message: self._append("error", message))
        self._dictation_timer = QTimer(self)
        self._dictation_timer.setInterval(200)
        self._dictation_timer.timeout.connect(self._update_dictation_button)
        self._proposals: list[dict] = []
        self._approvals_dialog = None
        self._funnel_pool = QThreadPool(self)
        self._funnel_pool.setMaxThreadCount(1)
        self._client = SessionClient(self, state=ctx.shared)

        self._client.connected.connect(self._on_connected)
        self._client.disconnected.connect(self._on_disconnected)
        self._client.failed.connect(self._on_failed)
        self._client.roster_changed.connect(self._on_roster)
        self._client.said.connect(self._on_said)
        self._client.rolled.connect(self._on_rolled)
        self._client.system.connect(self._append_system)
        # The DM's panel names arrive with the campaign; hand them to the
        # shell, which re-titles the docks.
        self._client.panel_names_received.connect(self._on_panel_names)
        self._client.proposals_received.connect(self._on_proposals)
        self._client.history_received.connect(self._on_history)
        self._client.autopilot_changed.connect(self._on_autopilot_changed)
        self._client.busy_changed.connect(self._on_busy_changed)
        self._client.spend_changed.connect(self._on_spend_changed)
        # A refusal has to reach the panel showing the character, not just the
        # chat: their screen still has the change they asked for on it.
        self._client.edit_refused.connect(ctx.bus.edit_refused)

        self._build_ui()
        # A share changed while hosting: push it to whoever may now see it.
        ctx.bus.share_changed.connect(self._on_share_changed)
        ctx.bus.player_edit_requested.connect(self._on_player_edit)
        ctx.bus.panel_names_changed.connect(self._on_panel_names_changed)
        ctx.bus.entity_changed.connect(self._on_share_changed)
        # Deletion too, or a character removed mid-session stays on every
        # player's screen until they reconnect. publish_entity turns a missing
        # entity into a removal, so the same handler covers it.
        ctx.bus.entity_deleted.connect(self._on_share_changed)
        # The canon log, for DM-role connections only -- an agent on autopilot
        # answers from it, so a fact committed mid-scene has to reach it.
        ctx.bus.fact_committed.connect(self._on_fact_committed)
        ctx.bus.theme_changed.connect(lambda _dark: self._refresh_colours())
        self._refresh_colours()
        self._update_state()

        # Launched by joining a session: connect straight away rather than
        # asking for the same credentials a second time.
        if ctx.pending_join is not None:
            url, username, password = ctx.pending_join
            ctx.pending_join = None
            self._append_system(f"Connecting to {url}...")
            self._client.join(url, username, password)

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        bar = QHBoxLayout()
        self._host_button = QPushButton("Go online")
        self._host_button.setToolTip(
            "Start a server for this campaign so players can join"
        )
        self._host_button.clicked.connect(self._host)
        bar.addWidget(self._host_button)

        self._join_button = QPushButton("Join session")
        self._join_button.clicked.connect(self._join)
        bar.addWidget(self._join_button)

        self._leave_button = QPushButton("Leave")
        self._leave_button.clicked.connect(self._leave)
        bar.addWidget(self._leave_button)

        self._funnel_button = QPushButton("Share on the internet")
        self._funnel_button.setCheckable(True)
        self._funnel_button.setToolTip(
            "Publish this session through Tailscale Funnel, so players outside "
            "your network can join. They do not need Tailscale."
        )
        self._funnel_button.clicked.connect(self._toggle_funnel)
        bar.addWidget(self._funnel_button)

        self._autopilot_button = QPushButton("Autopilot")
        self._autopilot_button.setCheckable(True)
        self._autopilot_button.setToolTip(
            "Let the agent answer for you. Press it again to take the table "
            "back -- it goes quiet immediately, mid-sentence if need be."
        )
        self._autopilot_button.clicked.connect(self._toggle_autopilot)
        bar.addWidget(self._autopilot_button)

        self._agent_button = QPushButton("Agent...")
        self._agent_button.setToolTip(
            "The key and model autopilot answers with, and how to change them."
        )
        self._agent_button.clicked.connect(self._show_agent_settings)
        bar.addWidget(self._agent_button)

        self._approvals_button = QPushButton("Waiting for you")
        self._approvals_button.setToolTip(
            "Changes players have asked for: levels, classes, ability scores"
        )
        self._approvals_button.clicked.connect(self._show_approvals)
        self._approvals_button.setVisible(False)
        bar.addWidget(self._approvals_button)

        self._players_button = QPushButton("Players...")
        self._players_button.setToolTip("Who may log in, and which character they play")
        self._players_button.clicked.connect(self._manage_accounts)
        self._players_button.setVisible(self._ctx.role == Role.DM.value)
        bar.addWidget(self._players_button)

        self._invite_button = QPushButton("Copy invite")
        self._invite_button.setToolTip("Copy the address players should connect to")
        self._invite_button.clicked.connect(self._copy_invite)
        bar.addWidget(self._invite_button)
        bar.addStretch(1)
        outer.addLayout(bar)

        # Who is composing, and -- for the DM -- what the agent has cost. Both
        # answer the same question: is anything actually happening?
        self._busy_label = QLabel("")
        self._busy_label.setWordWrap(True)
        self._busy_label.setVisible(False)
        outer.addWidget(self._busy_label)

        self._spend_label = QLabel("")
        self._spend_label.setVisible(False)
        self._spend_label.setToolTip(
            "What autopilot has cost this session, as the agent reports it."
        )
        outer.addWidget(self._spend_label)

        self._status = QLabel("Not connected.")
        self._status.setWordWrap(True)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self._status)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        splitter.addWidget(self._log)

        self._roster = QListWidget()
        self._roster.setMaximumWidth(190)
        self._roster.setToolTip("Who is connected")
        splitter.addWidget(self._roster)
        splitter.setStretchFactor(0, 1)
        outer.addWidget(splitter, 1)

        dice_row = QHBoxLayout()
        dice_row.setSpacing(4)
        for die in QUICK_DICE:
            button = QPushButton(die)
            button.setMaximumWidth(48)
            button.clicked.connect(lambda _c=False, d=die: self._roll(d))
            dice_row.addWidget(button)
        dice_row.addStretch(1)
        outer.addLayout(dice_row)

        entry = QHBoxLayout()
        self._entry = QLineEdit()
        self._entry.setPlaceholderText("Say something, or /roll 2d6+3")
        self._entry.returnPressed.connect(self._send)
        entry.addWidget(self._entry, 1)

        self._show_chatter = QCheckBox("Show log")
        self._show_chatter.setToolTip(
            "The app talking about itself: who arrived and left, autopilot "
            "switching, and anything else that is not the game. Hidden by "
            "default. Things addressed to you are always shown."
        )
        self._show_chatter.toggled.connect(self._on_log_toggled)
        outer.addWidget(self._show_chatter)

        self._say_button = QPushButton("Speak")
        self._say_button.setCheckable(True)
        self._say_button.setMaximumWidth(96)
        self._say_button.clicked.connect(self._toggle_dictation)
        if Dictation.is_available():
            self._say_button.setToolTip(
                "Talk instead of typing. What you said appears here for you to "
                "correct before you send it."
            )
        else:
            self._say_button.setEnabled(False)
            self._say_button.setToolTip(Dictation.unavailable_hint())
        entry.addWidget(self._say_button)

        send = QPushButton("Send")
        send.clicked.connect(self._send)
        entry.addWidget(send)
        outer.addLayout(entry)

    def _refresh_colours(self) -> None:
        dark = self.palette().base().color().lightness() < 128
        self._colours = {
            "system": QColor("#8b8b8b" if dark else "#767676"),
            "roll": QColor("#e5c07b" if dark else "#8a6100"),
            "dm": QColor("#7aa2f7" if dark else "#1a5fb4"),
            "player": QColor("#7fd1a0" if dark else "#1c7048"),
            "error": QColor("#ff6b6b" if dark else "#b00020"),
        }
        if getattr(self, "_show_chatter", None) is not None and self._show_chatter.styleSheet():
            self._flag_log()

    # -------------------------------------------------------------- dictation

    def _toggle_dictation(self) -> None:
        if self._say_button.isChecked():
            if not self._dictation.start():
                self._say_button.setChecked(False)
                return
            self._dictation_timer.start()
            self._update_dictation_button()
        else:
            self._dictation_timer.stop()
            self._say_button.setText("Speak")
            self._dictation.stop(self._glossary())

    def _update_dictation_button(self) -> None:
        seconds = int(self._dictation.elapsed)
        self._say_button.setText(f"Stop {seconds // 60}:{seconds % 60:02d}")

    def _on_dictated(self, text: str) -> None:
        """Put the words in the box. Sending stays a deliberate act.

        Appended rather than replacing, so a second sentence adds to the first
        instead of throwing it away.
        """
        existing = self._entry.text().strip()
        self._entry.setText(f"{existing} {text}".strip() if existing else text)
        self._entry.setFocus()
        self._entry.end(False)

    def _on_dictation_status(self, text: str) -> None:
        if text:
            self._ctx.bus.status_message.emit(text)

    def _glossary(self) -> str:
        """Proper nouns to prime the transcriber with.

        The DM has the campaign to draw on. A player has only what the host
        chose to send them, which is exactly the set of names they are likely
        to say out loud.
        """
        if self._ctx.role == Role.DM.value:
            from canon_keeper.audio.transcribe import build_glossary

            return build_glossary(self._ctx.repos, self._ctx.campaign_id)

        if self._ctx.shared is None:
            return ""
        names = [
            entity.get("name", "")
            for entity in self._ctx.shared.all()
            if entity.get("name")
        ]
        if not names:
            return ""
        return "The following names may be mentioned: " + ", ".join(names) + "."

    # ---------------------------------------------------------------- actions

    def _default_name(self) -> str:
        return self._ctx.repos.settings.get("session_name", "") or "Dungeon Master"

    def _on_player_edit(self, entity_id: int, changes: dict) -> None:
        if not self._client.send_edit(entity_id, changes):
            self._append("error", "Not connected, so that change was not saved.")

    def _on_proposals(self, proposals: list) -> None:
        """The DM's queue changed. Make it visible without being a nuisance."""
        self._proposals = proposals
        waiting = len(proposals)
        self._approvals_button.setVisible(waiting > 0)
        self._approvals_button.setText(
            f"Waiting for you ({waiting})" if waiting else "Waiting for you"
        )
        if self._approvals_dialog is not None:
            self._approvals_dialog.set_proposals(proposals)

    def _show_approvals(self) -> None:
        dialog = ApprovalsDialog(self._proposals, self)
        dialog.decided.connect(self._decide_proposal)
        self._approvals_dialog = dialog
        dialog.exec()
        self._approvals_dialog = None

    def _decide_proposal(self, proposal_id: int, approve: bool, note: str = "") -> None:
        # Through the client, so the host applies it -- even when the host is us.
        self._client.send_decision(proposal_id, approve, note)

    def _on_panel_names(self, names: dict) -> None:
        if self._ctx.names is not None:
            self._ctx.names.apply_party_names(names)

    def _on_panel_names_changed(self) -> None:
        # The DM renamed something while hosting: tell the table.
        if self._server is not None and self._server.is_running:
            self._server.publish_panel_names()

    def _on_share_changed(self, entity_id: int) -> None:
        if self._server is None or not self._server.is_running:
            return
        if not self._relaying_player_edit:
            # A change of *yours* refuses anything a player proposed against the
            # older sheet. A player's own edit must not: it would cancel the
            # level-up they asked for the moment they took damage.
            self._server.refuse_conflicting(entity_id)
        self._server.publish_entity(entity_id)

    def _toggle_autopilot(self) -> None:
        if self._server is None or not self._server.is_running:
            self._autopilot_button.setChecked(False)
            return

        if not self._autopilot_button.isChecked():
            self._server.set_autopilot(False, by=self._my_name())
            # The agent is stopped after the switch, not before: turning it off
            # is what silences it, and that has already happened by here.
            self._stop_agent()
            self._update_state()
            return

        if not self._ensure_agent_running():
            self._autopilot_button.setChecked(False)
            self._update_state()
            return

        self._server.set_autopilot(True, by=self._my_name())
        self._update_state()

    def _my_name(self) -> str:
        me = self._client.me
        return me.name if me else "the DM"

    def _ensure_agent_running(self) -> bool:
        """Make sure something is there to answer. Returns False if not.

        An agent someone started themselves -- on this machine or a spare box --
        is left alone. Only when nothing is connected does the app start one,
        because pressing a button should not require a terminal.
        """
        if self._server is None:
            return False
        if any(m.role == Role.AGENT.value for m in self._server.members):
            return True
        if self._agent_process is not None and self._agent_process.poll() is None:
            return True  # started, still connecting

        problem = agent_runner.missing_requirement()
        if problem:
            QMessageBox.information(self, "The agent cannot run", problem)
            return False

        key = agent_runner.api_key()
        if not key:
            key = self._ask_for_api_key()
            if not key:
                return False

        try:
            username, password = agent_runner.ensure_account(
                self._ctx.repos, self._ctx.campaign_id
            )
            self._agent_process = agent_runner.start(
                f"ws://127.0.0.1:{self._server.port}",
                username,
                password,
                key,
                self._agent_model(),
                agent_runner.workspace_id(),
            )
        except agent_runner.AgentUnavailable as exc:
            QMessageBox.warning(self, "Cannot start the agent", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - surfaced, never fatal
            self._ctx.log.exception("could not start the agent")
            QMessageBox.warning(self, "Cannot start the agent", str(exc))
            return False

        self._agent_watchdog.start()
        self._append("system", "Starting the agent...")
        return True

    def _check_agent(self) -> None:
        """Notice when the agent stops, and say why."""
        agent = self._agent_process
        if agent is None:
            self._agent_watchdog.stop()
            return
        if agent.poll() is None:
            return  # still running

        self._agent_watchdog.stop()
        self._agent_process = None
        reason = agent.why_it_stopped()
        self._ctx.log.warning("the agent stopped (%s): %s", agent.returncode, reason)
        self._append("error", f"The agent stopped. {reason}")

        # Autopilot without an agent is a table waiting on nothing.
        if self._server is not None and self._server.is_running:
            self._server.set_autopilot(False, by=self._my_name())
        self._autopilot_button.setChecked(False)
        self._update_state()

    def _ask_for_api_key(self) -> str:
        """Open the agent settings so a first-time key can be typed.

        Returns the key to use now. On a machine with no credential store the
        dialog saves nothing, so what it was given is returned directly rather
        than read back from a store that did not keep it.
        """
        dialog = AgentSettingsDialog(self._ctx, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""
        return dialog.key

    def _show_agent_settings(self) -> None:
        """Reachable whether or not a key is set -- otherwise a mistyped one is
        permanent, and the only symptom is an agent that never answers."""
        AgentSettingsDialog(self._ctx, self).exec()

    def _agent_model(self) -> str:
        return self._ctx.repos.settings.get(MODEL_SETTING, "")

    def _stop_agent(self) -> None:
        self._agent_watchdog.stop()
        if self._agent_process is None:
            return
        # Only ours. An agent someone else started is not the app's to kill.
        agent_runner.stop(self._agent_process)
        self._agent_process = None

    def _on_busy_changed(self, member, on: bool) -> None:
        """Show that someone is composing.

        Held as a set rather than a flag: two people can be typing at once, and
        one of them finishing must not clear the other.
        """
        label = getattr(member, "label", "") or "Someone"
        if on:
            self._busy.add(label)
        else:
            self._busy.discard(label)

        if not self._busy:
            self._busy_label.setVisible(False)
            return

        names = sorted(self._busy)
        if len(names) == 1:
            text = f"{names[0]} is writing..."
        else:
            text = f"{', '.join(names[:-1])} and {names[-1]} are writing..."
        self._busy_label.setText(text)
        self._busy_label.setVisible(True)

    def _on_spend_changed(self, spend: dict) -> None:
        """The agent's running bill, on the DM's screen only."""
        dollars = float(spend.get("dollars") or 0.0)
        turns = int(spend.get("turns") or 0)
        if not turns:
            self._spend_label.setVisible(False)
            return

        went_in = int(spend.get("tokens_in") or 0)
        came_out = int(spend.get("tokens_out") or 0)
        cost = f"${dollars:.2f}" if dollars >= 0.005 else "under a cent"
        if dollars <= 0:
            # An unpriced model: report the tokens rather than invent a figure.
            cost = "cost unknown for this model"
        self._spend_label.setText(
            f"Autopilot: {turns} answer{'s' if turns != 1 else ''}, "
            f"{went_in:,} in / {came_out:,} out, {cost}"
        )
        self._spend_label.setVisible(True)

    def _on_autopilot_changed(self, on: bool, by: str) -> None:
        """Someone flipped the switch -- reflect it without echoing it back."""
        self._autopilot_button.blockSignals(True)
        self._autopilot_button.setChecked(on)
        self._autopilot_button.blockSignals(False)
        self._autopilot_button.setText("Autopilot on" if on else "Autopilot")
        self._update_state()

    def _on_fact_committed(self, _fact_id: int) -> None:
        if self._server is not None and self._server.is_running:
            self._server.publish_facts()

    def _on_player_edit_applied(self, entity_id: int) -> None:
        """Tell the DM's own panels to re-read what a player just changed."""
        self._relaying_player_edit = True
        try:
            self._ctx.bus.entity_changed.emit(entity_id)
        finally:
            self._relaying_player_edit = False

    def _toggle_funnel(self) -> None:
        if self._funnel_button.isChecked():
            self._start_funnel()
        else:
            self._stop_funnel()

    def _start_funnel(self) -> None:
        if self._server is None or not self._server.is_running:
            self._append("error", "Go online first, then share it on the internet.")
            self._funnel_button.setChecked(False)
            return

        self._funnel_button.setEnabled(False)
        self._funnel_button.setText("Starting...")
        self._append_system("Asking Tailscale to publish this session...")
        self._run_funnel(funnel.start, self._server.port, self._on_funnel_started)

    def _stop_funnel(self) -> None:
        self._funnel_button.setEnabled(False)
        self._funnel_url = ""
        self._proposals: list[dict] = []
        self._approvals_dialog = None
        self._run_funnel(funnel.stop, None, self._on_funnel_stopped)

    def _run_funnel(self, action, argument, on_done) -> None:
        task = (
            _FunnelTask(action, argument) if argument is not None else _FunnelTask(action)
        )
        task.signals.done.connect(on_done)
        self._funnel_pool.start(task)

    def _on_funnel_started(self, result) -> None:
        self._funnel_button.setEnabled(True)
        if not result.ok:
            self._funnel_button.setChecked(False)
            self._funnel_button.setText("Share on the internet")
            self._report_funnel_problem(result)
            self._update_state()
            return

        self._funnel_url = result.websocket_url
        self._funnel_button.setText("Stop sharing")
        self._append_system(f"Published. Players outside your network: {self._funnel_url}")
        self._append_system("Use Copy invite to send that to them.")
        self._update_state()

    def _report_funnel_problem(self, result) -> None:
        """Say what went wrong, and where it can be fixed if there is a link.

        Tailscale's own wording names the setting to change, so it is shown
        unaltered rather than paraphrased.
        """
        box = QMessageBox(self)
        box.setWindowTitle("Share on the internet")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(result.message)

        if result.enable_url:
            open_button = box.addButton(
                "Open Tailscale settings", QMessageBox.ButtonRole.AcceptRole
            )
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is open_button:
                QDesktopServices.openUrl(QUrl(result.enable_url))
                self._append_system(
                    "Turn Funnel on in the page that just opened, then press "
                    "Share on the internet again."
                )
                return
        else:
            box.setStandardButtons(QMessageBox.StandardButton.Close)
            box.exec()

        self._append("error", "Could not publish the session.")

    def _on_funnel_stopped(self, result) -> None:
        self._funnel_button.setEnabled(True)
        self._funnel_button.setText("Share on the internet")
        if result.ok:
            self._append_system("No longer shared on the internet.")
        else:
            self._append("error", result.message or "Could not stop sharing.")
        self._update_state()

    def _copy_invite(self) -> None:
        """Whatever address a player should actually be given."""
        if self._funnel_url:
            invite = self._funnel_url
        elif self._server is not None and self._server.is_running:
            addresses = discovery.local_addresses()
            host = next(
                (a for a in sorted(addresses) if not a.startswith("127.")), "127.0.0.1"
            )
            invite = f"ws://{host}:{self._server.port}"
        else:
            return
        QApplication.clipboard().setText(invite)
        self._ctx.bus.status_message.emit(f"Copied {invite}")

    def _manage_accounts(self) -> None:
        AccountsDialog(self._ctx, self).exec()

    def _host(self) -> None:
        campaign = self._ctx.repos.campaigns.get(self._ctx.campaign_id)
        campaign_name = campaign.name if campaign else "this campaign"

        if not self._ctx.repos.accounts.players(self._ctx.campaign_id):
            proceed = QMessageBox.question(
                self,
                "Host a session",
                "Nobody has a login for this campaign yet, so no player could "
                "join. Host anyway?",
            )
            if proceed != QMessageBox.StandardButton.Yes:
                self._manage_accounts()
                return

        dialog = HostDialog(campaign_name, self._default_name(), self)
        if not dialog.exec():
            return
        name, port, session_name = dialog.values()

        server = SessionServer(
            self._ctx.repos, self._ctx.campaign_id, session_name, parent=self
        )
        server.failed.connect(self._on_failed)
        server.entity_applied.connect(self._on_player_edit_applied)
        if not server.start(port):
            server.deleteLater()
            return

        self._server = server
        self._ctx.repos.settings.set("session_name", name)
        self._append_system(f"Hosting {session_name!r} on port {server.port}.")
        # Join our own server like anyone else, so there is one path through the
        # code -- with a token, since we already own the campaign file.
        self._client.join_as_host(f"ws://127.0.0.1:{server.port}", server.local_token, name)
        self._update_state()

    def _join(self) -> None:
        dialog = JoinDialog(
            self._ctx.repos.settings.get("session_last_url", ""),
            self._ctx.repos.settings.get("session_username", ""),
            self,
        )
        if not dialog.exec():
            return
        url, username, password = dialog.values()
        if not username or not password:
            self._append("error", "A username and password are needed to join.")
            return

        self._pending_credentials = (
            (url, username, password) if dialog.should_remember() else None
        )
        if not dialog.should_remember():
            credentials.forget(url, username)

        self._ctx.repos.settings.set("session_last_url", url)
        self._ctx.repos.settings.set("session_username", username)
        self._append_system(f"Connecting to {url}...")
        self._client.join(url, username, password)
        self._update_state()

    def _leave(self) -> None:
        self._client.leave()
        if self._funnel_button.isChecked():
            # Closing the session must also take it off the public internet,
            # or the tunnel outlives the thing it was pointing at.
            self._funnel_button.setChecked(False)
            self._stop_funnel()
        if self._server is not None:
            self._server.stop()
            self._server.deleteLater()
            self._server = None
            self._append_system("Session closed.")
        self._update_state()

    def _send(self) -> None:
        text = self._entry.text().strip()
        if not text:
            return
        if text.startswith("/"):
            self._command(text)
        elif self._client.send_chat(text):
            self._entry.clear()

    def _command(self, text: str) -> None:
        head, _, rest = text[1:].partition(" ")
        head = head.lower()
        if head in ("roll", "r"):
            if self._roll(rest.strip()):
                self._entry.clear()
        else:
            self._append("error", f"Unknown command /{head}. Try /roll 2d6+3.")

    def _roll(self, notation: str) -> bool:
        if not notation:
            self._append("error", "What should I roll? Try /roll 2d6+3.")
            return False
        # Rolled by the host, not here, so the result is not ours to fake.
        return self._client.send_roll(notation)

    # ----------------------------------------------------------------- events

    def _on_connected(self) -> None:
        # Only now do we know the credentials were good, so only now save them.
        if self._pending_credentials is not None:
            url, username, password = self._pending_credentials
            self._pending_credentials = None
            campaigns.remember_remote(url, self._client.me.name if self._client.me else "", username)
            if credentials.save(url, username, password):
                self._append_system("Password saved for this session.")
        self._update_state()

    def _on_disconnected(self) -> None:
        self._roster.clear()
        self._update_state()

    def _on_failed(self, message: str) -> None:
        self._append("error", message)
        self._update_state()

    def _on_roster(self, members: list[Member]) -> None:
        self._roster.clear()
        for member in members:
            label = member.label
            if member.character and member.character != member.name:
                label = f"{member.character}  ({member.name})"
            item = QListWidgetItem(
                f"{label}  -  {'DM' if member.role == Role.DM.value else 'player'}"
            )
            item.setForeground(self._colours.get(member.role, self._colours["player"]))
            self._roster.addItem(item)

    def _on_said(self, member: Member, text: str) -> None:
        self._append(member.role, f"{member.label}: {text}")

    def _on_rolled(self, member: Member, payload: dict) -> None:
        self._append("roll", f"{member.label} rolled {payload.get('description', '')}")

    # ------------------------------------------------------------------ output

    def _on_history(self, messages: list) -> None:
        """Replay what was said before we arrived.

        The log is cleared first: this is the authoritative account, and our own
        "Connecting..." lines are not part of it.
        """
        self._log.clear()
        self._entries.clear()
        for entry in messages:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind", "system")
            speaker = entry.get("speaker", "")
            text = str(entry.get("text", ""))
            if kind == "said" and speaker:
                text = f"{speaker}: {text}"
            elif kind == "rolled" and speaker:
                text = f"{speaker} rolled {text}"
            elif kind == "system":
                # Old joins and leaves are the same housekeeping as live ones,
                # and hiding one while showing the other would be odd.
                kind = "chatter"
            self._append(
                entry.get("role") or kind,
                text,
                when=entry.get("at"),
            )
        if messages:
            self._append("system", "--- you are here ---")

    def _append(self, kind: str, text: str, when: float | None = None) -> None:
        """Record a line, and show it if it is not being filtered out."""
        self._entries.append((kind, text, when or datetime.now().timestamp()))
        if kind == "error":
            # Some errors answer a button the reader just pressed, and the log
            # is the wrong place to learn that: it is filtered, and they are
            # looking at the button. The status bar is immediate and transient,
            # which is exactly the right weight for it.
            self._ctx.bus.status_message.emit(text)
        if self._is_hidden(kind):
            if kind == "error":
                # And a mark on the filter, so a problem is not both hidden and
                # unannounced.
                self._flag_log()
            return
        self._draw(kind, text, when)

    def _flag_log(self) -> None:
        colour = self._colours.get("error", QColor("#b00020")).name()
        self._show_chatter.setStyleSheet(f"color: {colour}; font-weight: 600;")
        self._show_chatter.setText("Show log  (something went wrong)")

    def _unflag_log(self) -> None:
        self._show_chatter.setStyleSheet("")
        self._show_chatter.setText("Show log")

    def _on_log_toggled(self, shown: bool) -> None:
        if shown:
            # Seen. The mark has done its job.
            self._unflag_log()
        self._redraw()

    #: What lives in the log rather than in the game: the app talking about
    #: itself. A refusal, a roll, an agent that could not answer are *notices*
    #: and are never in here -- they are the reason someone is reading.
    LOG_KINDS = ("chatter", "error")

    def _is_hidden(self, kind: str) -> bool:
        return kind in self.LOG_KINDS and not self._show_chatter.isChecked()

    def _redraw(self) -> None:
        """Rebuild the whole log, which is what makes the filter reversible."""
        self._log.clear()
        for kind, text, when in self._entries:
            if not self._is_hidden(kind):
                self._draw(kind, text, when)

    def _draw(self, kind: str, text: str, when: float | None = None) -> None:
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        stamp = QTextCharFormat()
        stamp.setForeground(self._colours["system"])
        body = QTextCharFormat()
        body.setForeground(self._colours.get(kind, self._colours["system"]))
        if kind in ("roll", "error"):
            body.setFontWeight(600)

        if not self._log.document().isEmpty():
            cursor.insertBlock()
        moment = datetime.fromtimestamp(when) if when else datetime.now()
        cursor.insertText(f"{moment:%H:%M}  ", stamp)
        cursor.insertText(text, body)
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())

    def _append_system(self, text: str, kind: str = "") -> None:
        """A line from the host. ``kind`` distinguishes chatter from a notice."""
        self._append("chatter" if kind == SystemKind.CHATTER.value else "system", text)

    def _update_state(self) -> None:
        connected = self._client.is_connected
        hosting = self._server is not None and self._server.is_running

        is_dm = self._ctx.role == Role.DM.value
        self._host_button.setEnabled(is_dm and not connected and not hosting)
        self._host_button.setVisible(is_dm)
        self._funnel_button.setVisible(is_dm)
        self._funnel_button.setEnabled(hosting)
        # Only the host can hand over what it is hosting.
        self._autopilot_button.setVisible(is_dm)
        self._autopilot_button.setEnabled(hosting)
        # Reachable without hosting: setting the key up front is a reasonable
        # thing to do before a session rather than during one.
        self._agent_button.setVisible(is_dm)
        self._invite_button.setVisible(is_dm)
        self._invite_button.setEnabled(hosting)
        self._join_button.setEnabled(not connected and not hosting)
        self._leave_button.setEnabled(connected or hosting)
        self._entry.setEnabled(connected)

        if hosting and self._server is not None:
            if self._funnel_url:
                self._status.setText(
                    f"Hosting on port {self._server.port}, and published at "
                    f"{self._funnel_url} for players anywhere."
                )
            else:
                self._status.setText(
                    f"Hosting on port {self._server.port}. Players on this network "
                    "will see it listed and log in with the accounts you gave them."
                )
        elif connected:
            me = self._client.me
            self._status.setText(f"Connected as {me.name}." if me else "Connected.")
        else:
            self._status.setText("Not connected.")

    def shutdown(self) -> None:
        """Close the socket, stop hosting, and unpublish. Called when the app exits."""
        # Before the socket goes: an agent left running against a dead session
        # is a process nobody knows to kill.
        self._stop_agent()
        self._dictation_timer.stop()
        self._dictation.cancel()
        self._client.leave()
        if self._funnel_url:
            funnel.stop()
            self._funnel_url = ""
        if self._server is not None:
            self._server.stop()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.shutdown()
        super().closeEvent(event)


__all__ = ["TableWidget", "DEFAULT_PORT", "discovery"]
