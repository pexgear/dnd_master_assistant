"""Speak instead of typing.

Record a moment of speech, get text back. What the caller does with the text is
its own business -- the chat box puts it in the input line rather than sending
it, because a transcription is a first draft and the person who said it is the
only one who can tell whether it came out right.

Everything happens locally: the clip never leaves the machine and there is
nothing to pay for. Without ``faster-whisper`` installed this reports itself
unavailable and the caller hides the button.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal

from canon_keeper.audio import capture, transcribe

log = logging.getLogger("canonkeeper.audio.dictation")


class Dictation(QObject):
    """One button's worth of speech-to-text."""

    #: Transcribed words, ready to be shown to whoever said them.
    text_ready = Signal(str)
    #: A status line: loading a model, transcribing, and so on.
    status = Signal(str)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None, model: str = "") -> None:
        super().__init__(parent)
        self._recorder = capture.Recorder(self)
        self._recorder.failed.connect(self.failed)

        self._pool = QThreadPool(self)
        # The model is not safe to drive from several threads at once, and
        # serialising also keeps a burst of clips from thrashing the CPU.
        self._pool.setMaxThreadCount(1)

        self._model = model or transcribe.DEFAULT_MODEL
        self._transcriber: transcribe.FasterWhisperTranscriber | None = None
        self._clip: Path | None = None

    # ------------------------------------------------------------- properties

    @staticmethod
    def is_available() -> bool:
        return transcribe.is_available()

    @staticmethod
    def unavailable_hint() -> str:
        return transcribe.INSTALL_HINT

    @property
    def is_recording(self) -> bool:
        return self._recorder.is_recording

    @property
    def elapsed(self) -> float:
        return self._recorder.elapsed

    def set_model(self, model: str) -> None:
        if model and model != self._model:
            self._model = model
            self._transcriber = None  # loaded lazily on the next clip

    # ---------------------------------------------------------------- recording

    def start(self, device=None) -> bool:
        if not self.is_available():
            self.failed.emit(self.unavailable_hint())
            return False
        return self._recorder.start(device)

    def stop(self, glossary: str = "") -> bool:
        """Stop, and transcribe what was said. Returns False if nothing was."""
        if not self._recorder.is_recording:
            return False

        # A dictated line is wanted as text, not as a recording, so the clip
        # goes to a temporary file and is deleted once it has been read.
        destination = Path(tempfile.gettempdir()) / f"canonkeeper-say-{time.time_ns()}.wav"
        clip = self._recorder.stop(destination)
        if clip is None:
            self.status.emit("")
            return False

        transcriber = self._ensure_transcriber()
        if transcriber is None:
            self._discard(clip)
            return False

        self.status.emit(
            "Transcribing..."
            if transcriber.is_loaded
            else f"Loading the {self._model} model (the first run downloads it)..."
        )
        task = transcribe.TranscriptionTask(transcriber, clip, glossary)
        task.signals.finished.connect(self._on_finished)
        task.signals.failed.connect(self._on_failed)
        self._pool.start(task)
        return True

    def cancel(self) -> None:
        self._recorder.cancel()
        self.status.emit("")

    # ----------------------------------------------------------------- internals

    def _ensure_transcriber(self):
        if not self.is_available():
            self.failed.emit(self.unavailable_hint())
            return None
        if self._transcriber is None:
            self._transcriber = transcribe.FasterWhisperTranscriber(self._model)
        return self._transcriber

    def _on_finished(self, clip: object, result: object) -> None:
        self.status.emit("")
        self._discard(Path(str(clip)))
        text = (result.text or "").strip()
        if text:
            self.text_ready.emit(text)
        else:
            self.status.emit("Nothing was heard.")

    def _on_failed(self, clip: object, message: str) -> None:
        self.status.emit("")
        self._discard(Path(str(clip)))
        self.failed.emit(message)

    @staticmethod
    def _discard(clip: Path) -> None:
        try:
            clip.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - a leftover temp file is harmless
            log.debug("could not remove %s", clip)
