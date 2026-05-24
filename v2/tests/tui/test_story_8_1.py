"""Story 8.1 — design tokens, glyphs, messages, coordinator, PipelineStrip, UISettings."""

from __future__ import annotations

import asyncio
import dataclasses
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from stackowl.config.settings import Settings, UISettings
from stackowl.events.bus import EventBus
from stackowl.tui.coordinator import UIStateCoordinator
from stackowl.tui.glyphs import (
    GLYPH_DNA_BARS,
    GLYPH_PARLIAMENT,
    GLYPH_PUSHBACK,
    GLYPH_SEPARATOR,
    GLYPH_STEP_COMPLETE,
    GLYPH_STEP_EMPTY,
    Glyph,
)
from stackowl.tui.messages import (
    DegradedProviderMessage,
    JobPausedMessage,
    PipelineStepMessage,
)
from stackowl.tui.widgets.pipeline_strip import PipelineStrip

pytestmark = pytest.mark.tui

_TCSS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "stackowl"
    / "tui"
    / "styles"
    / "stackowl.tcss"
)
_CONTRAST_PATH = _TCSS_PATH.parent / "contrast_pairs.yaml"


_EXPECTED_TOKENS: tuple[str, ...] = (
    "$color-bg",
    "$color-bg-elevated",
    "$color-surface",
    "$color-border",
    "$color-text-primary",
    "$color-text-secondary",
    "$color-text-muted",
    "$color-accent",
    "$color-accent-dim",
    "$color-success",
    "$color-warning",
    "$color-error",
    "$color-parliament",
    "$font-mono",
    "$radius-sm",
    "$radius-md",
)


class _FakeApp:
    """Minimal Textual.App stand-in capturing posted messages."""

    def __init__(self) -> None:
        self.posted: list[Any] = []

    def call_from_thread(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    def post_message(self, message: Any) -> None:
        self.posted.append(message)


# ---------------------------------------------------------------------------
# A. Design tokens / stylesheet
# ---------------------------------------------------------------------------


def test_all_16_design_tokens_present() -> None:
    text = _TCSS_PATH.read_text(encoding="utf-8")
    missing = [tok for tok in _EXPECTED_TOKENS if tok not in text]
    assert not missing, f"Missing tokens in stackowl.tcss: {missing}"
    assert len(_EXPECTED_TOKENS) == 16


def test_tcss_defines_three_layers() -> None:
    text = _TCSS_PATH.read_text(encoding="utf-8")
    assert "layers:" in text, "Screen.layers declaration missing"
    layer_line = next(line for line in text.splitlines() if "layers:" in line)
    for layer in ("base", "overlay", "top"):
        assert layer in layer_line, f"layer {layer!r} missing in {layer_line!r}"


def test_contrast_pairs_yaml_has_at_least_8_pairs() -> None:
    raw = yaml.safe_load(_CONTRAST_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert "pairs" in raw
    pairs = raw["pairs"]
    assert isinstance(pairs, list)
    assert len(pairs) >= 8, f"need ≥8 contrast pairs, got {len(pairs)}"


# ---------------------------------------------------------------------------
# B. Glyph behavior
# ---------------------------------------------------------------------------


def test_glyph_str_returns_char_when_unicode_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STACKOWL_NO_GLYPHS", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    g = Glyph("◆", "<>")
    assert str(g) == "◆"


def test_glyph_str_returns_fallback_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STACKOWL_NO_GLYPHS", "1")
    g = Glyph("◆", "<>")
    assert str(g) == "<>"


def test_all_glyph_chars_are_bmp() -> None:
    glyphs: list[Glyph] = [
        GLYPH_STEP_COMPLETE,
        GLYPH_STEP_EMPTY,
        GLYPH_SEPARATOR,
        GLYPH_PARLIAMENT,
        GLYPH_PUSHBACK,
        *GLYPH_DNA_BARS,
    ]
    for g in glyphs:
        for ch in g.char:
            assert ord(ch) <= 0xFFFF, (
                f"glyph char {ch!r} (U+{ord(ch):04X}) is outside the BMP"
            )


# ---------------------------------------------------------------------------
# C. Message dataclasses
# ---------------------------------------------------------------------------


def test_pipeline_step_message_is_frozen_dataclass() -> None:
    msg = PipelineStepMessage(step_name="brief", step_index=2, total_steps=8)
    assert dataclasses.is_dataclass(msg)
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.step_name = "other"  # type: ignore[misc]


def test_degraded_provider_message_is_frozen_dataclass() -> None:
    msg = DegradedProviderMessage(provider_name="openai", tier="fast", reason="quota")
    assert dataclasses.is_dataclass(msg)
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.tier = "cheap"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# D. PipelineStrip widget
# ---------------------------------------------------------------------------


def test_pipeline_strip_render_shows_step_glyphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STACKOWL_NO_GLYPHS", "1")
    strip = PipelineStrip()
    strip.step_index = 3
    strip.total_steps = 5
    rendered = strip.render()
    row1 = rendered.splitlines()[0]
    # 3 complete '*' followed by 2 empty 'o'
    assert row1.count("*") == 3
    assert row1.count("o") == 2


def test_pipeline_strip_render_shows_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STACKOWL_NO_GLYPHS", "1")
    strip = PipelineStrip()
    strip.active_agents = 2
    strip.provider = "openai"
    strip.tier = "fast"
    strip.cost_today = 1.234
    rendered = strip.render()
    row2 = rendered.splitlines()[1]
    assert "active_agents=2" in row2
    assert "provider=openai:fast" in row2
    assert "cost_today=$1.23" in row2


def test_pipeline_strip_handler_updates_state() -> None:
    strip = PipelineStrip()
    msg = PipelineStepMessage(step_name="memory", step_index=4, total_steps=8)
    strip.on_pipeline_step_message(msg)
    assert strip.step_name == "memory"
    assert strip.step_index == 4
    assert strip.total_steps == 8


# ---------------------------------------------------------------------------
# E. UIStateCoordinator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_dispatch_pipeline_step_changed() -> None:
    bus = EventBus()
    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=bus)  # type: ignore[arg-type]
    await coord._dispatch(
        "pipeline_step_changed",
        {"step_name": "brief", "step_index": 1, "total_steps": 8},
    )
    assert len(app.posted) == 1
    assert isinstance(app.posted[0], PipelineStepMessage)
    assert app.posted[0].step_name == "brief"


@pytest.mark.asyncio
async def test_coordinator_dispatch_job_paused() -> None:
    bus = EventBus()
    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=bus)  # type: ignore[arg-type]
    await coord._dispatch(
        "job_paused",
        {"job_id": "j1", "handler": "h", "last_error": "boom"},
    )
    assert len(app.posted) == 1
    assert isinstance(app.posted[0], JobPausedMessage)
    assert app.posted[0].job_id == "j1"


@pytest.mark.asyncio
async def test_coordinator_queue_serializes_events() -> None:
    bus = EventBus()
    app = _FakeApp()
    coord = UIStateCoordinator(app=app, event_bus=bus)  # type: ignore[arg-type]
    await coord.start()
    try:
        # Emit events synchronously — they will be enqueued then drained in order.
        bus.emit("pipeline_step_changed", {
            "step_name": "a", "step_index": 1, "total_steps": 8,
        })
        bus.emit("pipeline_step_changed", {
            "step_name": "b", "step_index": 2, "total_steps": 8,
        })
        bus.emit("pipeline_step_changed", {
            "step_name": "c", "step_index": 3, "total_steps": 8,
        })
        # Give the consumer a couple of event-loop ticks to drain.
        for _ in range(20):
            if len(app.posted) >= 3:
                break
            await asyncio.sleep(0.01)
        assert len(app.posted) == 3
        names = [m.step_name for m in app.posted]
        assert names == ["a", "b", "c"], (
            f"events delivered out of order: {names}"
        )
    finally:
        await coord.stop()


# ---------------------------------------------------------------------------
# F. UISettings
# ---------------------------------------------------------------------------


def test_ui_settings_defaults() -> None:
    ui = UISettings()
    assert ui.language == "auto"
    assert ui.tui_version == "v2"
    assert ui.reduced_motion is False


def test_settings_includes_ui_block() -> None:
    os.environ.pop("STACKOWL_CONFIG_FILE", None)
    settings = Settings()
    assert isinstance(settings.ui, UISettings)
    assert settings.ui.tui_version == "v2"
