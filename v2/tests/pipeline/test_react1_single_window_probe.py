"""REACT-1 / F032 + F090 — provider + model window resolved ONCE per turn.

assemble resolves and STAMPS state.model_window. execute must then consume that
stamped value and issue NO second window probe on the steady path. The only place
execute may probe is the FALLBACK branch (a route reached _run_with_tools without
assemble, model_window is None) — and that probe must be BOUNDED with a safe
default so the hot tool-loop entry can never hang.
"""
from __future__ import annotations

import asyncio

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import execute as execute_mod
from stackowl.pipeline.steps.execute import _resolve_execute_window
from stackowl.providers.model_window import DEFAULT_WINDOW_FALLBACK


class _FakeProvider:
    """Minimal provider carrying a config the fallback probe would read."""

    name = "ollama_local"
    protocol = "openai"

    class _Cfg:
        base_url = "http://localhost:11434"
        default_model = "gemma:12b"
        context_chars = None

    _config = _Cfg()


def _state(model_window: int | None) -> PipelineState:
    return PipelineState(
        trace_id="t", session_id="s", input_text="do it", channel="cli",
        owl_name="o", pipeline_step="execute", model_window=model_window,
    )


@pytest.mark.asyncio
async def test_steady_path_uses_stamped_window_no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """When assemble stamped state.model_window, execute issues ZERO window probes."""
    calls = {"n": 0}

    async def _spy(**_kw: object) -> int:
        calls["n"] += 1
        return 999

    monkeypatch.setattr(execute_mod, "resolve_window", _spy)

    window = await _resolve_execute_window(_state(model_window=8192), _FakeProvider())  # type: ignore[arg-type]

    assert window == 8192, "execute must consume the value assemble stamped"
    assert calls["n"] == 0, "no second probe on the steady path"


@pytest.mark.asyncio
async def test_fallback_path_probes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a stamped window, execute issues exactly ONE bounded probe."""
    calls = {"n": 0}

    async def _spy(**_kw: object) -> int:
        calls["n"] += 1
        return 4096

    monkeypatch.setattr(execute_mod, "resolve_window", _spy)

    window = await _resolve_execute_window(_state(model_window=None), _FakeProvider())  # type: ignore[arg-type]

    assert window == 4096
    assert calls["n"] == 1, "fallback resolves the window with a single probe"


@pytest.mark.asyncio
async def test_fallback_probe_is_bounded_with_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung probe must NOT hang the turn — the deadline fires and a safe default
    window is returned (the bound is explicit at the call site)."""
    async def _hang(**_kw: object) -> int:
        await asyncio.sleep(60)  # never completes within the deadline
        return 1

    monkeypatch.setattr(execute_mod, "resolve_window", _hang)
    monkeypatch.setattr(execute_mod, "_WINDOW_PROBE_DEADLINE_S", 0.05)

    window = await asyncio.wait_for(
        _resolve_execute_window(_state(model_window=None), _FakeProvider()),  # type: ignore[arg-type]
        timeout=2.0,  # the helper's OWN deadline must trip well before this
    )

    assert window == DEFAULT_WINDOW_FALLBACK, "timeout must fall back to the safe default"
