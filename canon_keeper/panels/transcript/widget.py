"""Record a beat, see it transcribed, fix it if Whisper got a name wrong.

The row appears the moment you stop recording, marked as pending, and fills in
when the model finishes. Nothing blocks the GUI thread, so a slow transcription
never freezes the app mid-session.

Rows stay editable on purpose. An utterance is the only thing allowed to be the
source of a fact, so the text you see here is the text everything downstream
will trust -- correcting a mangled name is the cheapest fix in the whole app.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioDevice
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from canon_keeper import config
from canon_keeper.audio import capture, transcribe
from canon_keeper.plugin import AppContext

RECORD_SHORTCUT = "F9"
_PENDING = "(transcribing...)"

COL_TIME = 0
COL_TEXT = 1


class TranscriptWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._loading = False
        self._rows_by_utterance: dict[int, int] = {}
        self._pending_by_audio: dict[str, int] = {}

        self._recorder = capture.Recorder(self)
        self._recorder.level_changed.connect(self._on_level)
        self._recorder.failed.connect(self._on_recorder_failed)

        self._pool = QThreadPool(self)
        # The model is not safe to drive from several threads, and serialising
        # also stops a burst of clips thrashing the CPU mid-session.
        self._pool.setMaxThreadCount(1)
        self._transcriber: transcribe.FasterWhisperTranscriber | None = None

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(200)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        self._session = ctx.repos.sessions.ensure_open(ctx.campaign_id)

        self._build_ui()
        ctx.bus.campaign_changed.connect(self._on_campaign_changed)

        self.reload()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        # --- toolbar ---
        bar = QHBoxLayout()

        self._record_button = QPushButton(f"Record  ({RECORD_SHORTCUT})")
        self._record_button.setCheckable(True)
        self._record_button.setMinimumWidth(170)
        self._record_button.clicked.connect(self._toggle_recording)
        bar.addWidget(self._record_button)

        self._level = QProgressBar()
        self._level.setRange(0, 100)
        self._level.setTextVisible(False)
        self._level.setFixedWidth(90)
        self._level.setToolTip("Input level")
        bar.addWidget(self._level)

        self._device_combo = QComboBox()
        self._device_combo.setToolTip("Microphone")
        self._device_combo.setMinimumWidth(180)
        bar.addWidget(self._device_combo, 1)

        self._model_combo = QComboBox()
        self._model_combo.setToolTip(
            "Whisper model. Larger is more accurate and slower; the first use of "
            "each downloads it."
        )
        for size in transcribe.MODEL_SIZES:
            self._model_combo.addItem(size)
        saved_model = self._ctx.repos.settings.get("whisper_model", transcribe.DEFAULT_MODEL)
        index = self._model_combo.findText(saved_model)
        self._model_combo.setCurrentIndex(index if index >= 0 else 0)
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        bar.addWidget(self._model_combo)

        outer.addLayout(bar)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)

        # --- the transcript ---
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Time", "What you said"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setWordWrap(True)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(COL_TIME, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_TEXT, QHeaderView.ResizeMode.Stretch)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        outer.addWidget(self._table, 1)

        # --- typed entry ---
        typed = QHBoxLayout()
        self._typed = QLineEdit()
        self._typed.setPlaceholderText("...or type a beat and press Enter")
        self._typed.returnPressed.connect(self._add_typed)
        typed.addWidget(self._typed, 1)
        add_button = QPushButton("Add")
        add_button.clicked.connect(self._add_typed)
        typed.addWidget(add_button)
        outer.addLayout(typed)

        self._shortcut = QShortcut(QKeySequence(RECORD_SHORTCUT), self)
        self._shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut.activated.connect(self._record_button.click)

        self._populate_devices()
        self._check_availability()

    def _populate_devices(self) -> None:
        self._device_combo.clear()
        self._device_combo.addItem("Default microphone", None)
        saved = self._ctx.repos.settings.get("audio_input_id", None)
        for device in capture.available_inputs():
            device_id = bytes(device.id()).decode("utf-8", "replace")
            self._device_combo.addItem(device.description(), device_id)
            if saved and device_id == saved:
                self._device_combo.setCurrentIndex(self._device_combo.count() - 1)
        self._device_combo.currentIndexChanged.connect(
            lambda _i: self._ctx.repos.settings.set(
                "audio_input_id", self._device_combo.currentData()
            )
        )

    def _check_availability(self) -> None:
        """Recording without transcription is a tape recorder, so gate on it."""
        if transcribe.is_available():
            self._status.setText("")
            return
        self._record_button.setEnabled(False)
        self._model_combo.setEnabled(False)
        self._status.setText(transcribe.INSTALL_HINT.replace("\n\n", "  ").replace("\n", "  "))
        self._status.setToolTip(transcribe.INSTALL_HINT)

    def _selected_device(self) -> QAudioDevice | None:
        wanted = self._device_combo.currentData()
        if wanted is None:
            return None
        for device in capture.available_inputs():
            if bytes(device.id()).decode("utf-8", "replace") == wanted:
                return device
        return None

    # -------------------------------------------------------------- recording

    def _toggle_recording(self) -> None:
        if self._record_button.isChecked():
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        if not self._recorder.start(self._selected_device()):
            self._record_button.setChecked(False)
            return
        self._record_button.setText("Stop  0:00")
        self._elapsed_timer.start()
        self._ctx.bus.status_message.emit("Recording...")

    def _stop_recording(self) -> None:
        self._elapsed_timer.stop()
        self._record_button.setText(f"Record  ({RECORD_SHORTCUT})")
        self._level.setValue(0)

        destination = self._audio_dir() / f"{datetime.now():%Y%m%d-%H%M%S}.wav"
        path = self._recorder.stop(destination)
        if path is None:
            self._ctx.bus.status_message.emit("Nothing was recorded.")
            return

        # The row is created now, with the audio attached, so a failed or slow
        # transcription can never orphan a recording.
        utterance = self._ctx.repos.utterances.add(
            self._session.id, "", audio_path=str(path)
        )
        self._append_row(utterance.id, utterance.t, _PENDING, pending=True)
        self._pending_by_audio[str(path)] = utterance.id

        self._queue_transcription(path)

    def _queue_transcription(self, path: Path) -> None:
        transcriber = self._ensure_transcriber()
        if transcriber is None:
            return
        prompt = transcribe.build_glossary(self._ctx.repos, self._ctx.campaign_id)
        task = transcribe.TranscriptionTask(transcriber, path, prompt)
        task.signals.started.connect(self._on_transcription_started)
        task.signals.finished.connect(self._on_transcribed)
        task.signals.failed.connect(self._on_transcription_failed)
        self._pool.start(task)

    def _ensure_transcriber(self) -> transcribe.FasterWhisperTranscriber | None:
        if not transcribe.is_available():
            self._check_availability()
            return None
        if self._transcriber is None:
            self._transcriber = transcribe.FasterWhisperTranscriber(
                self._model_combo.currentText()
            )
        return self._transcriber

    def _update_elapsed(self) -> None:
        seconds = int(self._recorder.elapsed)
        self._record_button.setText(f"Stop  {seconds // 60}:{seconds % 60:02d}")

    def _on_level(self, level: float) -> None:
        self._level.setValue(int(level * 100))

    def _on_recorder_failed(self, message: str) -> None:
        self._record_button.setChecked(False)
        self._record_button.setText(f"Record  ({RECORD_SHORTCUT})")
        self._elapsed_timer.stop()
        QMessageBox.warning(self, "Microphone", message)

    def _on_model_changed(self, model: str) -> None:
        self._ctx.repos.settings.set("whisper_model", model)
        # Drop the loaded model; the next clip loads the newly chosen one.
        self._transcriber = None
        self._ctx.bus.status_message.emit(f"Whisper model set to {model}")

    # ----------------------------------------------------------- transcription

    def _on_transcription_started(self, path: object) -> None:
        loaded = self._transcriber is not None and self._transcriber.is_loaded
        self._status.setText(
            "Transcribing..."
            if loaded
            else f"Loading the {self._model_combo.currentText()} model "
            "(the first run downloads it)..."
        )

    def _on_transcribed(self, path: object, result: object) -> None:
        self._status.setText("")
        utterance_id = self._pending_by_audio.pop(str(path), None)
        if utterance_id is None:
            return

        text = result.text.strip() or "(nothing was heard)"
        self._ctx.repos.utterances.update_text(utterance_id, result.text.strip())
        self._set_row_text(utterance_id, text, pending=not result.text.strip())
        self._ctx.bus.utterance_added.emit(utterance_id)
        self._ctx.bus.status_message.emit(f"Transcribed {result.duration:.0f}s of audio")

    def _on_transcription_failed(self, path: object, message: str) -> None:
        self._status.setText(f"Transcription failed: {message}")
        utterance_id = self._pending_by_audio.pop(str(path), None)
        if utterance_id is not None:
            # The audio survives, so the row can be retried or typed in by hand.
            self._set_row_text(utterance_id, "(transcription failed)", pending=True)

    # ------------------------------------------------------------------ table

    def reload(self) -> None:
        self._loading = True
        self._table.setRowCount(0)
        self._rows_by_utterance.clear()
        for utterance in self._ctx.repos.utterances.for_session(self._session.id):
            text = utterance.text or _PENDING
            self._append_row(
                utterance.id, utterance.t, text, pending=not utterance.text, scroll=False
            )
        self._loading = False
        self._scroll_to_bottom()

    def _append_row(
        self, utterance_id: int, t: float, text: str, pending: bool = False, scroll: bool = True
    ) -> None:
        was_loading = self._loading
        self._loading = True

        row = self._table.rowCount()
        self._table.insertRow(row)

        time_item = QTableWidgetItem(datetime.fromtimestamp(t).strftime("%H:%M:%S"))
        time_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        time_item.setData(Qt.ItemDataRole.UserRole, utterance_id)
        self._table.setItem(row, COL_TIME, time_item)

        text_item = QTableWidgetItem(text)
        text_item.setData(Qt.ItemDataRole.UserRole, utterance_id)
        if pending:
            text_item.setForeground(Qt.GlobalColor.gray)
        self._table.setItem(row, COL_TEXT, text_item)
        self._table.resizeRowToContents(row)

        self._rows_by_utterance[utterance_id] = row
        self._loading = was_loading
        if scroll:
            self._scroll_to_bottom()

    def _set_row_text(self, utterance_id: int, text: str, pending: bool = False) -> None:
        row = self._rows_by_utterance.get(utterance_id)
        if row is None:
            return
        was_loading = self._loading
        self._loading = True
        item = self._table.item(row, COL_TEXT)
        if item is not None:
            item.setText(text)
            item.setForeground(
                Qt.GlobalColor.gray if pending else self._table.palette().text().color()
            )
            self._table.resizeRowToContents(row)
        self._loading = was_loading

    def _scroll_to_bottom(self) -> None:
        self._table.scrollToBottom()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """A hand-corrected transcription is the version everything else trusts."""
        if self._loading or item.column() != COL_TEXT:
            return
        utterance_id = item.data(Qt.ItemDataRole.UserRole)
        if utterance_id is None:
            return
        text = item.text().strip()
        if text in (_PENDING, "(nothing was heard)", "(transcription failed)"):
            return
        self._ctx.repos.utterances.update_text(utterance_id, text)
        item.setForeground(self._table.palette().text().color())
        self._ctx.bus.utterance_added.emit(utterance_id)

    def _show_context_menu(self, position) -> None:
        item = self._table.itemAt(position)
        if item is None:
            return
        utterance_id = item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        act_copy = QAction("Copy text", self)
        act_copy.triggered.connect(lambda: self._copy_row(utterance_id))
        menu.addAction(act_copy)

        act_retry = QAction("Transcribe again", self)
        act_retry.triggered.connect(lambda: self._retry(utterance_id))
        act_retry.setEnabled(transcribe.is_available())
        menu.addAction(act_retry)

        menu.addSeparator()
        act_delete = QAction("Delete", self)
        act_delete.triggered.connect(lambda: self._delete(utterance_id))
        menu.addAction(act_delete)

        menu.exec(self._table.viewport().mapToGlobal(position))

    def _copy_row(self, utterance_id: int) -> None:
        from PySide6.QtWidgets import QApplication

        utterance = self._ctx.repos.utterances.get(utterance_id)
        if utterance:
            QApplication.clipboard().setText(utterance.text)
            self._ctx.bus.status_message.emit("Copied")

    def _retry(self, utterance_id: int) -> None:
        utterance = self._ctx.repos.utterances.get(utterance_id)
        if utterance is None or not utterance.audio_path:
            self._ctx.bus.status_message.emit("That line has no audio to re-transcribe.")
            return
        path = Path(utterance.audio_path)
        if not path.exists():
            self._ctx.bus.status_message.emit(f"Audio file is missing: {path.name}")
            return
        self._set_row_text(utterance_id, _PENDING, pending=True)
        self._pending_by_audio[str(path)] = utterance_id
        self._queue_transcription(path)

    def _delete(self, utterance_id: int) -> None:
        self._ctx.repos.utterances.delete(utterance_id)
        self.reload()

    # ------------------------------------------------------------------ typed

    def _add_typed(self) -> None:
        text = self._typed.text().strip()
        if not text:
            return
        utterance = self._ctx.repos.utterances.add(self._session.id, text, t=time.time())
        self._append_row(utterance.id, utterance.t, text)
        self._typed.clear()
        self._ctx.bus.utterance_added.emit(utterance.id)

    # --------------------------------------------------------------- campaign

    def _on_campaign_changed(self, campaign_id: int) -> None:
        if self._recorder.is_recording:
            self._recorder.cancel()
            self._record_button.setChecked(False)
            self._record_button.setText(f"Record  ({RECORD_SHORTCUT})")
            self._elapsed_timer.stop()
        self._session = self._ctx.repos.sessions.ensure_open(campaign_id)
        self.reload()

    def _audio_dir(self) -> Path:
        return config.data_dir() / "audio" / f"session_{self._session.id}"
