"""KuzuAdapter HealableResource conformance + GraphContributor live-probe fix
(ADR-6 self-heal, Task 3).

``ensure_available()`` tears down and reconstructs the ``Database``/``Connection``
handles ENTIRELY on the F067-confined single Kuzu worker thread (never on the
calling thread/task), then lets failure propagate so the sweep's
RecoveryActuator owns retry/backoff — mirrors LanceDBAdapter (Task 2).

``GraphContributor`` previously only checked ``import kuzu`` succeeds and would
report healthy even with a dead live adapter connection. The critical guard
here is the same anti-mistake test as Task 2's LanceDB suite: a real outage
reported by the live adapter's ``health()`` must surface as ``down``, never be
silently upgraded to ``ok`` by a successful import check.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.health.contributors import GraphContributor
from stackowl.memory.bridge import HealthReport
from stackowl.memory.kuzu_adapter import KuzuAdapter

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def _kuzu_available() -> None:
    pytest.importorskip("kuzu")


@pytest.fixture(autouse=True)
def _allow_live_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None))


async def test_ensure_available_reconstructs_db_and_conn_on_confined_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _kuzu_available: None
) -> None:
    """A dead db/conn is torn down and rebuilt — entirely on the single kuzu worker
    thread, never the calling task's thread (F067 thread-confinement)."""
    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    try:
        first_db = adapter._db  # type: ignore[attr-defined]
        first_conn = adapter._conn  # type: ignore[attr-defined]

        seen_threads: set[int] = set()
        real_construct = adapter._construct_db_and_conn  # type: ignore[attr-defined]

        def _wrapped() -> tuple[object, object]:
            seen_threads.add(threading.get_ident())
            return real_construct()

        monkeypatch.setattr(adapter, "_construct_db_and_conn", _wrapped)

        await adapter.ensure_available()

        assert adapter._conn is not None  # type: ignore[attr-defined]
        assert adapter._db is not None  # type: ignore[attr-defined]
        assert adapter._conn is not first_conn  # type: ignore[attr-defined] — genuinely rebuilt
        assert adapter._db is not first_db  # type: ignore[attr-defined]
        # Reconstruction ran on the confined worker thread — never the calling
        # (event-loop / test) thread.
        assert len(seen_threads) == 1
        assert seen_threads != {threading.get_ident()}
    finally:
        await adapter.aclose()


async def test_ensure_available_propagates_and_marks_unavailable_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _kuzu_available: None
) -> None:
    """A reconstruct failure raises (no swallow) and available/unavailable_reason flip."""
    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    try:
        assert adapter.available is True

        def _boom() -> tuple[object, object]:
            raise RuntimeError("boom: kuzu native lib gone")

        monkeypatch.setattr(adapter, "_construct_db_and_conn", _boom)

        with pytest.raises(RuntimeError, match="boom: kuzu native lib gone"):
            await adapter.ensure_available()

        assert adapter.available is False
        assert adapter.unavailable_reason is not None
        assert "boom" in adapter.unavailable_reason
    finally:
        await adapter.aclose()


async def test_available_and_unavailable_reason_reflect_adapter_state(
    tmp_path: Path, _kuzu_available: None
) -> None:
    """A freshly-constructed adapter (schema bootstrapped in __init__) is available."""
    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    try:
        assert adapter.available is True
        assert adapter.unavailable_reason is None
    finally:
        await adapter.aclose()


async def test_register_on_recycled_is_a_noop_callback_registration(
    tmp_path: Path, _kuzu_available: None
) -> None:
    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    try:
        # Must not raise — no downstream dependents cache the raw conn/db (each
        # op reads self._conn fresh at call time), mirrors LanceDBAdapter.
        adapter.register_on_recycled(lambda: None)
    finally:
        await adapter.aclose()


# ----- GraphContributor live-probe fix (the regression this task closes) -------


async def test_graph_contributor_reports_down_when_live_adapter_health_is_down(
    tmp_path: Path, _kuzu_available: None
) -> None:
    """Anti-Kuzu-mistake guard: `import kuzu` succeeding must NOT mask a dead
    live adapter connection — this is the exact regression GraphContributor.probe()
    had before this fix (it only checked import success)."""
    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    try:
        adapter.health = AsyncMock(  # type: ignore[method-assign]
            return_value=HealthReport(
                name="memory.kuzu",
                status="down",
                details={"error": "RuntimeError: connection gone"},
                latency_ms=5.0,
            )
        )
        contributor = GraphContributor(available=True, adapter=adapter)

        status = await contributor.health_check()

        assert status.status == "down"
        assert status.message is not None
    finally:
        await adapter.aclose()


async def test_graph_contributor_reports_ok_when_live_adapter_health_is_ok(
    tmp_path: Path, _kuzu_available: None
) -> None:
    """Sanity check for the opposite direction — a genuinely healthy live probe
    must not be flagged down."""
    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    try:
        contributor = GraphContributor(available=True, adapter=adapter)

        status = await contributor.health_check()

        assert status.status == "ok"
    finally:
        await adapter.aclose()


async def test_graph_contributor_probe_without_adapter_stays_import_only() -> None:
    """`GraphContributor.probe()` (out-of-process CLI use, no live adapter — must
    NOT open the live DB the serve process holds) is unchanged: cached
    available/reason from the import check, no live probe attempted."""
    contributor = GraphContributor.probe()
    assert contributor.contributor_name == "graph"
    # No adapter wired — health_check must use the cached import-check result,
    # not attempt any live probe.
    status = await contributor.health_check()
    assert status.status in ("ok", "down")


async def test_graph_contributor_name_matches_healers_dict_key_in_real_assembly(
    tmp_db: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _kuzu_available: None
) -> None:
    """contributor_name MUST match the key `scheduler/assembly.py` registers in
    `healers`, or health_sweep's dict.get(status.name) lookup silently no-ops
    (the exact Task-1 embeddings mismatch this arc already hit once). Builds
    the REAL SchedulerAssembly and reads BOTH sides off the live objects —
    never a hardcoded literal compared against another hardcoded literal.

    `MemoryAssembly.build` opens Kuzu at `StackowlHome.home() / "kuzu"` with no
    override param, so STACKOWL_HOME is redirected to an isolated tmp dir —
    otherwise this collides with a live `stackowl serve` process's file lock on
    the real ~/.stackowl/kuzu/graph.kuzu (a known Kuzu single-writer issue Task
    2's implementer also hit)."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "stackowl_home"))
    from stackowl.config.settings import MemorySettings, Settings
    from stackowl.events.bus import EventBus
    from stackowl.memory.assembly import MemoryAssembly
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
    from stackowl.pipeline.services import StepServices
    from stackowl.providers.base import CompletionResult, Message, ModelProvider
    from stackowl.providers.registry import ProviderRegistry
    from stackowl.scheduler.assembly import SchedulerAssembly
    from stackowl.scheduler.base import HandlerRegistry
    from stackowl.skills.assembly import SkillsAssembly
    from stackowl.tools.registry import ToolRegistry

    class _StubProvider(ModelProvider):
        @property
        def name(self) -> str:
            return "stub"

        @property
        def protocol(self) -> str:  # type: ignore[override]
            return "openai"

        async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:  # noqa: ARG002
            return CompletionResult(
                content="", input_tokens=0, output_tokens=0,
                model="stub", provider_name="stub", duration_ms=0.0,
            )

        async def stream(self, messages, model, **kwargs):  # type: ignore[no-untyped-def]  # noqa: ARG002
            if False:  # pragma: no cover
                yield ""
            return

    HandlerRegistry.reset()
    try:
        settings = Settings(memory=MemorySettings())
        provider_registry = ProviderRegistry()
        provider_registry.register_mock("stub", _StubProvider(), tier="powerful")
        memory_components = await MemoryAssembly.build(
            db=tmp_db, settings=settings, provider_registry=provider_registry,
        )
        try:
            owl_registry = OwlRegistry()
            backend = AsyncioBackend(services=StepServices())
            skills_components = await SkillsAssembly.build(
                db=tmp_db, tool_registry=ToolRegistry(), owl_registry=owl_registry,
                skills_root=None, builtin_seed_dir=None,
            )
            components = await SchedulerAssembly.build(
                db=tmp_db, settings=settings, event_bus=EventBus(),
                provider_registry=provider_registry, owl_registry=owl_registry,
                memory_components=memory_components, backend=backend,
                skills_components=skills_components,
            )
            handler = components.health_sweep_handler
            registered_names = {
                c.contributor_name for c in handler._aggregator._contributors  # type: ignore[attr-defined]
            }
            assert memory_components.graph_health.contributor_name in registered_names
            assert (
                memory_components.graph_health.contributor_name in handler._healers  # type: ignore[attr-defined]
            )
            assert (
                handler._healers[memory_components.graph_health.contributor_name]  # type: ignore[attr-defined]
                is memory_components.kuzu_adapter
            )
        finally:
            if memory_components.kuzu_adapter is not None:
                await memory_components.kuzu_adapter.aclose()
    finally:
        HandlerRegistry.reset()
