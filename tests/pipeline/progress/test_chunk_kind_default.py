"""ResponseChunk.kind defaults to 'answer' — existing sites stay byte-identical."""

from __future__ import annotations

from stackowl.config.progress_settings import ProgressSettings
from stackowl.config.settings import Settings
from stackowl.pipeline.streaming import ResponseChunk


def test_chunk_kind_defaults_to_answer() -> None:
    chunk = ResponseChunk(
        content="hello", is_final=False, chunk_index=0, trace_id="t", owl_name="o"
    )
    assert chunk.kind == "answer"


def test_chunk_kind_can_be_progress() -> None:
    chunk = ResponseChunk(
        content="🔎 Searching the web…",
        is_final=False,
        chunk_index=0,
        trace_id="t",
        owl_name="o",
        kind="progress",
    )
    assert chunk.kind == "progress"


def test_progress_settings_defaults_on() -> None:
    s = ProgressSettings()
    assert s.live_progress is True
    assert s.telegram_edit_min_interval_s == 1.0
    assert s.typing_reissue_interval_s == 4.0


def test_settings_exposes_progress_block() -> None:
    s = Settings()
    assert isinstance(s.progress, ProgressSettings)
    assert s.progress.live_progress is True
