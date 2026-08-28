"""Local speech-to-text.

Runs entirely on your machine: no audio leaves it, and there is nothing to pay
for. ``faster-whisper`` is an optional extra, so everything here degrades to a
clear "not installed" rather than an import error at startup.

The single highest-value trick is the glossary. Whisper renders "Cragmaw" as
"crag more" and your canon quietly gains a location. Feeding the campaign's
proper nouns in as ``initial_prompt`` fixes most of that for the cost of one
string, and the string is regenerated from the entity table every time -- so the
more you write down, the better the transcription gets.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# Hugging Face warns on every Windows launch that it cannot make symlinks in its
# cache without Developer Mode. It is cosmetic and there is nothing the user
# needs to do about it.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from PySide6.QtCore import QObject, QRunnable, Signal

log = logging.getLogger("canonkeeper.transcribe")

#: Ordered smallest to largest. The default is a compromise: `small` handles
#: invented fantasy names far better than `base` and still runs in a couple of
#: seconds per clip on a laptop CPU.
MODEL_SIZES = ("tiny", "base", "small", "medium", "distil-large-v3", "large-v3")
DEFAULT_MODEL = "small"

#: Whisper only looks at roughly the last 224 tokens of the prompt, so there is
#: no point sending a whole campaign's worth of names.
MAX_GLOSSARY_CHARS = 800


#: Substrings that identify "you asked for a GPU you cannot actually use".
_CUDA_ERROR_HINTS = ("cublas", "cudnn", "cuda", "libcu", "no gpu", "cusparse")


def _is_missing_cuda(exc: Exception) -> bool:
    return any(hint in str(exc).lower() for hint in _CUDA_ERROR_HINTS)


def is_available() -> bool:
    """True when local transcription can actually run."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


INSTALL_HINT = (
    "Local transcription is not installed.\n\n"
    "Install it with:\n"
    "    pip install faster-whisper\n"
    "or, from a source checkout:\n"
    "    pip install -e .[whisper]"
)


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    language: str = ""
    duration: float = 0.0
    segments: list[str] = field(default_factory=list)


class Transcriber(Protocol):
    def transcribe(self, audio: Path, initial_prompt: str = "") -> TranscriptionResult: ...


def build_glossary(repos, campaign_id: int) -> str:
    """Proper nouns from the campaign, as a sentence to prime Whisper with.

    Regenerate this before every transcription: a name entered five minutes ago
    should be recognised on the next beat.
    """
    names: list[str] = []
    for entity in repos.entities.list(campaign_id):
        names.append(entity.name)
        names.extend(entity.aliases)

    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        cleaned = name.strip()
        # "New character" and friends are placeholders, not proper nouns.
        if not cleaned or cleaned.lower() in seen or cleaned.lower().startswith("new "):
            continue
        seen.add(cleaned.lower())
        unique.append(cleaned)

    if not unique:
        return ""

    prompt = "The following names may be mentioned: " + ", ".join(unique) + "."
    if len(prompt) > MAX_GLOSSARY_CHARS:
        prompt = prompt[:MAX_GLOSSARY_CHARS].rsplit(",", 1)[0] + "."
    return prompt


class FasterWhisperTranscriber:
    """Wraps ``faster_whisper.WhisperModel``, loading it on first use.

    Model construction downloads roughly half a gigabyte the first time and
    takes a few seconds after that, so it is deliberately deferred out of
    application startup and into the first transcription -- which happens on a
    worker thread.
    """

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        device: str = "auto",
        compute_type: str = "int8",
    ) -> None:
        self.model_size = model_size
        self.device = device
        # int8 is the safe choice across CPU and GPU. float16 is faster on a
        # real GPU but fails outright on CPU-only machines.
        self.compute_type = compute_type
        self._model = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("loading whisper model %r (%s)", self.model_size, self.device)
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
            log.info("whisper model ready")
        return self._model

    def transcribe(self, audio: Path, initial_prompt: str = "") -> TranscriptionResult:
        try:
            return self._run(audio, initial_prompt)
        except Exception as exc:  # noqa: BLE001 - inspected and re-raised below
            if self.device == "cpu" or not _is_missing_cuda(exc):
                raise
            # device="auto" happily selects CUDA on any machine with an NVIDIA
            # card, but ctranslate2 does not ship cuBLAS or cuDNN -- so a GPU
            # without the CUDA runtime installed fails here, at the first
            # inference, not at construction. Drop to CPU and get on with it.
            log.warning("GPU transcription unavailable (%s); falling back to CPU", exc)
            self.device = "cpu"
            self._model = None
            return self._run(audio, initial_prompt)

    def _run(self, audio: Path, initial_prompt: str = "") -> TranscriptionResult:
        model = self.load()
        segments, info = model.transcribe(
            str(audio),
            initial_prompt=initial_prompt or None,
            beam_size=5,
            # Drops the silence either side of a push-to-talk clip, which is
            # both faster and stops Whisper hallucinating into the gaps.
            vad_filter=True,
            condition_on_previous_text=False,
        )
        pieces = [segment.text.strip() for segment in segments]
        pieces = [piece for piece in pieces if piece]
        return TranscriptionResult(
            text=" ".join(pieces).strip(),
            language=getattr(info, "language", "") or "",
            duration=float(getattr(info, "duration", 0.0) or 0.0),
            segments=pieces,
        )


class TranscriptionSignals(QObject):
    finished = Signal(object, object)  # (Path, TranscriptionResult)
    failed = Signal(object, str)  # (Path, message)
    started = Signal(object)  # (Path,)


class TranscriptionTask(QRunnable):
    """Runs one transcription off the GUI thread.

    Submit these to a QThreadPool with ``setMaxThreadCount(1)``: the model is
    not safe to drive from several threads at once, and serialising also keeps
    a burst of clips from thrashing the CPU mid-session.
    """

    def __init__(self, transcriber: Transcriber, audio: Path, initial_prompt: str = "") -> None:
        super().__init__()
        self.signals = TranscriptionSignals()
        self._transcriber = transcriber
        self._audio = audio
        self._prompt = initial_prompt

    def run(self) -> None:  # noqa: D102 - QRunnable entry point
        self.signals.started.emit(self._audio)
        try:
            result = self._transcriber.transcribe(self._audio, self._prompt)
        except Exception as exc:  # noqa: BLE001 - report, never take the app down
            log.exception("transcription failed for %s", self._audio)
            self.signals.failed.emit(self._audio, str(exc))
            return
        self.signals.finished.emit(self._audio, result)
