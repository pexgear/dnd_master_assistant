"""Transcript panel behaviour, with a stand-in for Whisper.

No microphone and no model here: the point is the bookkeeping around them --
that a clip is never orphaned by a slow or failed transcription, and that a
hand-corrected line is what gets persisted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from canon_keeper.audio.transcribe import TranscriptionResult
from canon_keeper.panels.transcript.widget import COL_TEXT, TranscriptWidget


@pytest.fixture
def panel(ctx, qtbot) -> TranscriptWidget:
    widget = TranscriptWidget(ctx)
    qtbot.addWidget(widget)
    return widget


def test_typed_entry_becomes_an_utterance(panel, ctx, qtbot):
    with qtbot.waitSignal(ctx.bus.utterance_added, timeout=1000):
        panel._typed.setText("The party arrives at Cragmaw Castle.")
        panel._add_typed()

    stored = ctx.repos.utterances.for_session(panel._session.id)
    assert [u.text for u in stored] == ["The party arrives at Cragmaw Castle."]
    assert panel._table.rowCount() == 1
    assert panel._typed.text() == "", "the box should clear, ready for the next beat"


def test_blank_typed_entry_is_ignored(panel, ctx):
    panel._typed.setText("   ")
    panel._add_typed()
    assert ctx.repos.utterances.for_session(panel._session.id) == []


def test_editing_a_row_rewrites_the_utterance(panel, ctx):
    panel._typed.setText("crag more castle")
    panel._add_typed()
    utterance_id = ctx.repos.utterances.for_session(panel._session.id)[0].id

    panel._table.item(0, COL_TEXT).setText("Cragmaw Castle")

    assert ctx.repos.utterances.get(utterance_id).text == "Cragmaw Castle"


def test_placeholder_text_is_never_persisted(panel, ctx):
    """The greyed '(transcribing...)' marker must not become the utterance."""
    utterance = ctx.repos.utterances.add(panel._session.id, "", audio_path="x.wav")
    panel._append_row(utterance.id, utterance.t, "(transcribing...)", pending=True)

    panel._table.item(0, COL_TEXT).setText("(transcribing...)")

    assert ctx.repos.utterances.get(utterance.id).text == ""


def test_finished_transcription_fills_the_pending_row(panel, ctx):
    audio = Path("clip.wav")
    utterance = ctx.repos.utterances.add(panel._session.id, "", audio_path=str(audio))
    panel._append_row(utterance.id, utterance.t, "(transcribing...)", pending=True)
    panel._pending_by_audio[str(audio)] = utterance.id

    panel._on_transcribed(audio, TranscriptionResult(text="Sildar bleeds out.", duration=4.0))

    assert ctx.repos.utterances.get(utterance.id).text == "Sildar bleeds out."
    assert panel._table.item(0, COL_TEXT).text() == "Sildar bleeds out."
    assert str(audio) not in panel._pending_by_audio


def test_failed_transcription_keeps_the_row_and_the_audio(panel, ctx):
    """A failure must leave something you can retype or retry, not vanish."""
    audio = Path("clip.wav")
    utterance = ctx.repos.utterances.add(panel._session.id, "", audio_path=str(audio))
    panel._append_row(utterance.id, utterance.t, "(transcribing...)", pending=True)
    panel._pending_by_audio[str(audio)] = utterance.id

    panel._on_transcription_failed(audio, "model not found")

    assert panel._table.rowCount() == 1
    assert "failed" in panel._table.item(0, COL_TEXT).text()
    assert ctx.repos.utterances.get(utterance.id).audio_path == str(audio)
    assert "model not found" in panel._status.text()


def test_reload_restores_the_session_transcript(panel, ctx, qtbot):
    for line in ("First beat.", "Second beat."):
        panel._typed.setText(line)
        panel._add_typed()

    reopened = TranscriptWidget(ctx)
    qtbot.addWidget(reopened)

    assert reopened._table.rowCount() == 2
    assert reopened._table.item(1, COL_TEXT).text() == "Second beat."


def test_recording_uses_one_open_session(panel, ctx):
    """Reopening the panel must not start a second session mid-evening."""
    first = panel._session.id
    assert ctx.repos.sessions.ensure_open(ctx.campaign_id).id == first
    assert len(ctx.repos.sessions.list(ctx.campaign_id)) == 1


def test_switching_campaign_swaps_the_transcript(panel, ctx, qtbot):
    panel._typed.setText("Beat in the first campaign.")
    panel._add_typed()

    other = ctx.repos.campaigns.create("Second Campaign")
    ctx.campaign_id = other.id
    ctx.bus.campaign_changed.emit(other.id)

    assert panel._session.campaign_id == other.id
    assert panel._table.rowCount() == 0
