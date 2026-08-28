"""Microphone capture, via Qt's own audio stack.

Deliberately QAudioSource rather than PortAudio/sounddevice: it ships with Qt,
so there is no extra native dependency to fail differently on three platforms,
and Qt does sample-rate conversion for us.

We ask for 16 kHz mono Int16 because that is exactly what Whisper wants, and we
write a plain PCM WAV with the stdlib. No codec negotiation, no ffmpeg on the
capture path, and the file is playable by anything if you need to check what was
actually said.
"""

from __future__ import annotations

import array
import logging
import time
import wave
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioFormat, QAudioSource, QMediaDevices

log = logging.getLogger("canonkeeper.audio")

TARGET_SAMPLE_RATE = 16_000
TARGET_CHANNELS = 1


def available_inputs() -> list[QAudioDevice]:
    return list(QMediaDevices.audioInputs())


def default_input() -> QAudioDevice:
    return QMediaDevices.defaultAudioInput()


def _negotiate_format(device: QAudioDevice) -> QAudioFormat:
    """Get as close to 16 kHz mono Int16 as the device will allow.

    Anything we cannot get natively is fixed up afterwards: a differing sample
    rate is left for the decoder to resample, and float samples are converted
    on the way to the WAV.
    """
    wanted = QAudioFormat()
    wanted.setSampleRate(TARGET_SAMPLE_RATE)
    wanted.setChannelCount(TARGET_CHANNELS)
    wanted.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    if device.isFormatSupported(wanted):
        return wanted

    # Second choice: the device's own preferred rate, but still mono Int16.
    preferred = device.preferredFormat()
    fallback = QAudioFormat()
    fallback.setSampleRate(preferred.sampleRate())
    fallback.setChannelCount(TARGET_CHANNELS)
    fallback.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    if device.isFormatSupported(fallback):
        log.info("device does not do 16 kHz; capturing at %d Hz", preferred.sampleRate())
        return fallback

    log.info(
        "falling back to the device preferred format: %d Hz, %d ch, %s",
        preferred.sampleRate(),
        preferred.channelCount(),
        preferred.sampleFormat(),
    )
    return preferred


def _to_int16_mono(raw: bytes, fmt: QAudioFormat) -> bytes:
    """Normalise captured bytes to signed 16-bit mono PCM."""
    sample_format = fmt.sampleFormat()

    if sample_format == QAudioFormat.SampleFormat.Int16:
        samples = array.array("h")
        samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    elif sample_format == QAudioFormat.SampleFormat.Float:
        floats = array.array("f")
        floats.frombytes(raw[: len(raw) - (len(raw) % 4)])
        samples = array.array(
            "h", (max(-32768, min(32767, int(value * 32767.0))) for value in floats)
        )
    elif sample_format == QAudioFormat.SampleFormat.UInt8:
        raw_bytes = array.array("B", raw)
        samples = array.array("h", ((value - 128) << 8 for value in raw_bytes))
    elif sample_format == QAudioFormat.SampleFormat.Int32:
        ints = array.array("i")
        ints.frombytes(raw[: len(raw) - (len(raw) % 4)])
        samples = array.array("h", (value >> 16 for value in ints))
    else:
        raise ValueError(f"unsupported sample format: {sample_format}")

    channels = fmt.channelCount()
    if channels > 1:
        # Average the channels rather than dropping all but the first, so a mic
        # wired to only the right channel is not silently discarded.
        mixed = array.array("h")
        for index in range(0, len(samples) - channels + 1, channels):
            mixed.append(sum(samples[index : index + channels]) // channels)
        samples = mixed

    if array.array("h", [1]).tobytes() != b"\x01\x00":  # pragma: no cover - big-endian
        samples.byteswap()
    return samples.tobytes()


def peak_level(pcm16: bytes) -> float:
    """Peak amplitude of a 16-bit PCM chunk, 0.0 to 1.0, for a level meter."""
    if not pcm16:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm16[: len(pcm16) - (len(pcm16) % 2)])
    if not samples:
        return 0.0
    return min(1.0, max(abs(value) for value in samples) / 32768.0)


class Recorder(QObject):
    """Records to memory, then writes one WAV when stopped.

    A push-to-talk clip is seconds long, so buffering it in RAM is simpler and
    safer than streaming to disk: nothing half-written is left behind if the app
    dies mid-beat.
    """

    level_changed = Signal(float)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source: QAudioSource | None = None
        self._io = None
        self._buffer = bytearray()
        self._format: QAudioFormat | None = None
        self._started_at: float = 0.0

    @property
    def is_recording(self) -> bool:
        return self._source is not None

    @property
    def elapsed(self) -> float:
        return (time.monotonic() - self._started_at) if self.is_recording else 0.0

    def start(self, device: QAudioDevice | None = None) -> bool:
        if self.is_recording:
            return True

        device = device if device is not None and not device.isNull() else default_input()
        if device.isNull():
            self.failed.emit("No microphone was found.")
            return False

        try:
            fmt = _negotiate_format(device)
            source = QAudioSource(device, fmt, self)
            io = source.start()
        except Exception as exc:  # noqa: BLE001 - surface it, do not crash the app
            log.exception("could not open the audio input")
            self.failed.emit(f"Could not open the microphone: {exc}")
            return False

        if io is None:
            self.failed.emit("The microphone could not be opened for reading.")
            return False

        self._source = source
        self._io = io
        self._format = fmt
        self._buffer = bytearray()
        self._started_at = time.monotonic()
        io.readyRead.connect(self._drain)
        log.info(
            "recording from %s at %d Hz, %d ch",
            device.description(),
            fmt.sampleRate(),
            fmt.channelCount(),
        )
        return True

    def _drain(self) -> None:
        if self._io is None or self._format is None:
            return
        chunk = bytes(self._io.readAll())
        if not chunk:
            return
        pcm = _to_int16_mono(chunk, self._format)
        self._buffer.extend(pcm)
        self.level_changed.emit(peak_level(pcm))

    def stop(self, destination: Path) -> Path | None:
        """Stop recording and write the WAV. Returns None if nothing was captured."""
        if self._source is None:
            return None

        self._drain()
        source, fmt = self._source, self._format
        self._source = None
        self._io = None
        try:
            source.stop()
        finally:
            source.deleteLater()

        data = bytes(self._buffer)
        self._buffer = bytearray()
        self.level_changed.emit(0.0)

        if not data or fmt is None:
            return None

        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(fmt.sampleRate())
            handle.writeframes(data)

        seconds = len(data) / 2 / fmt.sampleRate()
        log.info("wrote %.1fs to %s", seconds, destination)
        return destination

    def cancel(self) -> None:
        """Throw away whatever is being recorded."""
        if self._source is None:
            return
        source = self._source
        self._source = None
        self._io = None
        self._buffer = bytearray()
        try:
            source.stop()
        finally:
            source.deleteLater()
        self.level_changed.emit(0.0)
