"""F-11 — normal user turns verify a declared/derived acceptance post-condition.

Before this fix the goal-level AcceptanceChecker ran ONLY in the objectives driver;
a normal turn that declared (or, with the flag-ON LLM layer, derived) an
``expected_outcome`` and then produced nothing was never checked, so the learner
could mine the false win. ``_verify_turn_acceptance`` invokes the checker on the
normal path, and a refuted verdict makes the captured outcome UNTRUSTWORTHY (so the
positive-only learner skips it) — exactly mirroring the unrecovered-effect path.

Default path (no declared outcome AND the LLM layer OFF) is a strict no-op → the
turn is byte-identical to pre-acceptance behavior.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

import stackowl.pipeline.backends.asyncio_backend as backend
from stackowl.objectives.model import ExpectedOutcome
from stackowl.pipeline.acceptance import AcceptanceVerdict
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

pytestmark = pytest.mark.asyncio


def _chunk(content: str) -> ResponseChunk:
    return ResponseChunk(
        content=content, is_final=True, chunk_index=0, trace_id="t1", owl_name="secretary",
    )


def _state(**kw: Any) -> PipelineState:
    base = dict(
        trace_id="t1",
        session_id="s1",
        input_text="do the thing",
        channel="cli",
        owl_name="secretary",
        pipeline_step="",
    )
    base.update(kw)
    return PipelineState(**base)


# ── _verify_turn_acceptance ──────────────────────────────────────────────────


async def test_no_outcome_no_tier_is_noop() -> None:
    """Default normal turn: no declared outcome, LLM layer OFF → no verdict."""
    state = _state(responses=(_chunk("done"),))
    verdict = await backend._verify_turn_acceptance(state, time.time(), StepServices())
    assert verdict is None


async def test_declared_artifact_refuted_when_no_file(tmp_path: Path) -> None:
    missing = tmp_path / "never-written"
    state = _state(
        expected_outcome=ExpectedOutcome(kind="artifact", artifact_dir=str(missing)),
        responses=(_chunk("all done!"),),
    )
    verdict = await backend._verify_turn_acceptance(state, time.time(), StepServices())
    assert verdict is not None
    assert verdict.accepted is False


async def test_declared_artifact_observed(tmp_path: Path) -> None:
    started = time.time()
    (tmp_path / "result.bin").write_bytes(b"x")
    state = _state(
        expected_outcome=ExpectedOutcome(kind="artifact", artifact_dir=str(tmp_path)),
        responses=(_chunk("all done!"),),
    )
    verdict = await backend._verify_turn_acceptance(state, started, StepServices())
    assert verdict is not None
    assert verdict.accepted is True


# ── _capture_outcome threads the verdict into the learning signal ────────────


class _FakeStore:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, _db: Any) -> None:
        pass

    async def record(self, **kwargs: Any) -> None:
        _FakeStore.last_kwargs = kwargs


async def test_capture_outcome_refuted_acceptance_is_untrustworthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend, "TaskOutcomeStore", _FakeStore)
    services = StepServices(db_pool=object())  # type: ignore[arg-type]
    state = _state(responses=(_chunk("all done!"),))
    await backend._capture_outcome(
        state, 12.0, services,
        acceptance=AcceptanceVerdict(False, "declared artifact, none produced"),
    )
    assert _FakeStore.last_kwargs["success"] is False
    assert _FakeStore.last_kwargs["failure_class"] == backend._UNACHIEVED_EFFECT_CLASS


async def test_capture_outcome_passing_acceptance_is_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend, "TaskOutcomeStore", _FakeStore)
    services = StepServices(db_pool=object())  # type: ignore[arg-type]
    state = _state(responses=(_chunk("all done!"),))
    await backend._capture_outcome(
        state, 12.0, services,
        acceptance=AcceptanceVerdict(True, "fresh artifact observed"),
    )
    assert _FakeStore.last_kwargs["success"] is True
    assert _FakeStore.last_kwargs["failure_class"] is None


async def test_capture_outcome_none_acceptance_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend, "TaskOutcomeStore", _FakeStore)
    services = StepServices(db_pool=object())  # type: ignore[arg-type]
    state = _state(responses=(_chunk("hi"),))
    await backend._capture_outcome(state, 12.0, services, acceptance=None)
    assert _FakeStore.last_kwargs["success"] is True
    assert _FakeStore.last_kwargs["failure_class"] is None
