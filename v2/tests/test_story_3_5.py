"""Story 3.5 — Parametrized backend tests covering AsyncioBackend & LangGraphBackend.

Every backend behaves identically per the ``OrchestratorBackend`` contract: the
8-step pipeline runs to completion, ``trace_id`` is preserved on the final
state, and step errors are captured in ``state.errors`` without short-circuiting
``deliver``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.backends.langgraph_backend import LangGraphBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_state(**kwargs: Any) -> PipelineState:
    defaults: dict[str, Any] = {
        "trace_id": "trace-3-5",
        "session_id": "sess-3-5",
        "input_text": "hello",
        "channel": "cli",
        "owl_name": "secretary",
        "pipeline_step": "",
    }
    defaults.update(kwargs)
    return PipelineState(**defaults)


@pytest.fixture(params=["asyncio", "langgraph"])
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[OrchestratorBackend]:
    """Parametrized backend fixture — every test runs against both backends."""
    services = StepServices()
    instance: OrchestratorBackend
    if request.param == "asyncio":
        instance = AsyncioBackend(services=services)
    else:
        # use_memory_checkpoint=True keeps tests hermetic (no sqlite file).
        instance = LangGraphBackend(services=services, use_memory_checkpoint=True)
    try:
        yield instance
    finally:
        await instance.shutdown()


# ---------------------------------------------------------------------------
# Conformance tests — must pass for every OrchestratorBackend implementation
# ---------------------------------------------------------------------------


async def test_backend_runs_pipeline(backend: OrchestratorBackend) -> None:
    """Pipeline runs end-to-end and finishes on the ``deliver`` step."""
    state = _make_state()
    result = await backend.run(state)
    assert result.pipeline_step == "deliver"
    assert result.errors == ()


async def test_backend_propagates_trace_id(backend: OrchestratorBackend) -> None:
    """The trace_id on the final state matches the input — preserved across steps."""
    state = _make_state(trace_id="my-trace-3-5")
    result = await backend.run(state)
    assert result.trace_id == "my-trace-3-5"


async def test_backend_captures_errors(backend: OrchestratorBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing step is captured in ``state.errors`` and the pipeline continues to deliver."""
    # Patch the canonical step function so BOTH backends see the failure.
    from stackowl.pipeline.steps import classify

    async def boom(state: PipelineState) -> PipelineState:
        raise RuntimeError("classify exploded")

    monkeypatch.setattr(classify, "run", boom)

    # ``PIPELINE_STEPS`` is a module-level list of (name, function-ref) tuples
    # evaluated at import time. Patching ``classify.run`` alone is not enough —
    # both backends import the list directly and bind the original refs at
    # construction. We replace the list in every module that imported it.
    from stackowl.pipeline import registry
    from stackowl.pipeline.backends import asyncio_backend as be_async
    from stackowl.pipeline.backends import langgraph_backend as be_lg
    from stackowl.pipeline.steps import (
        consolidate,
        dispatch,
        execute,
        parliament_step,
        synthesize,
        triage,
    )

    patched_steps = [
        ("triage", triage.run),
        ("dispatch", dispatch.run),
        ("classify", boom),
        ("execute", execute.run),
        ("parliament_step", parliament_step.run),
        ("consolidate", consolidate.run),
        ("synthesize", synthesize.run),
    ]
    monkeypatch.setattr(registry, "PIPELINE_STEPS", patched_steps)
    monkeypatch.setattr(be_async, "PIPELINE_STEPS", patched_steps)
    monkeypatch.setattr(be_lg, "PIPELINE_STEPS", patched_steps)

    services = StepServices()
    backend_under_test: OrchestratorBackend
    if isinstance(backend, LangGraphBackend):
        backend_under_test = LangGraphBackend(services=services, use_memory_checkpoint=True)
    else:
        backend_under_test = AsyncioBackend(services=services)

    try:
        result = await backend_under_test.run(_make_state())
    finally:
        await backend_under_test.shutdown()

    assert any("classify" in e for e in result.errors), result.errors
    assert result.pipeline_step == "deliver"


# ---------------------------------------------------------------------------
# LangGraph-specific guards
# ---------------------------------------------------------------------------


async def test_langgraph_backend_is_orchestrator_backend() -> None:
    backend = LangGraphBackend(use_memory_checkpoint=True)
    try:
        assert isinstance(backend, OrchestratorBackend)
    finally:
        await backend.shutdown()


async def test_langgraph_backend_shutdown_is_idempotent() -> None:
    """Calling shutdown() twice must not raise — backends may be torn down repeatedly."""
    backend = LangGraphBackend(use_memory_checkpoint=True)
    await backend.shutdown()
    await backend.shutdown()


async def test_langgraph_backend_sqlite_checkpointer(tmp_path: Any) -> None:
    """When use_memory_checkpoint=False, the backend wires AsyncSqliteSaver against the given path."""
    db_path = tmp_path / "lg_test.db"
    backend = LangGraphBackend(db_path=db_path, use_memory_checkpoint=False)
    try:
        result = await backend.run(_make_state(session_id="sess-sqlite"))
        assert result.pipeline_step == "deliver"
        assert db_path.exists(), "AsyncSqliteSaver should have created the db file"
    finally:
        await backend.shutdown()
