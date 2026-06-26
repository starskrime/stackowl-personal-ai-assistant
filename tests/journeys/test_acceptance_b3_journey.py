"""Goal-level acceptance → objectives driver, end-to-end (Branch 3).

THE integration test for the verification primitive's goal-level authority: a
sub-goal that DECLARES an expected artifact (``acceptance_criteria``) but whose
run produces NO file on disk must be marked ``failed`` — and its objective
``blocked`` — even though the model reported a confident "All done!". This is the
deterministic net above the per-tool ``verified`` net (B1/B2): it catches the
shell/``yt-dlp --simulate`` class, where the tool itself cannot self-verify,
because the turn declared the expected outcome UP FRONT.

The positive case proves byte-safety: when the run actually produces a fresh
artifact under the declared directory, the sub-goal is ``done`` as before; and a
sub-goal with NO acceptance_criteria falls back to the legacy no-error path
(byte-identical).

Drives the REAL ``ObjectiveDriverHandler`` + ``ObjectiveStore`` over a real
migrated DB; only the pipeline backend (sub-goal execution) is stubbed, exactly
as the live scheduler would supply it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.notifications.proactive_job import ProactiveDeliveryOutcome
from stackowl.objectives.driver import ObjectiveDriverHandler
from stackowl.objectives.model import ExpectedOutcome, Objective, SubgoalSpec
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.state import PipelineState, ToolCall
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.scheduler.job import Job
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "acceptance_b3.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


class _ToolEffectBackend:
    """Emulate a sub-goal run: the model claims success (a confident response +
    a recorded tool call), and OPTIONALLY writes a fresh artifact into
    ``produce_dir``. Mirrors what AsyncioBackend hands the driver, so the driver's
    acceptance gating is exercised on a realistic final_state."""

    def __init__(self, *, produce_dir: Path | None) -> None:
        self.produce_dir = produce_dir
        self.runs = 0

    async def run(self, state: PipelineState) -> PipelineState:
        self.runs += 1
        if self.produce_dir is not None:
            self.produce_dir.mkdir(parents=True, exist_ok=True)
            (self.produce_dir / "result.bin").write_bytes(b"real downloaded bytes")
        chunk = ResponseChunk(
            content="All done! I downloaded it successfully.", is_final=False,
            chunk_index=0, trace_id=state.trace_id, owl_name=state.owl_name,
        )
        ran = ToolCall(tool_name="shell", args={}, result="exit 0", error=None, duration_ms=1.0)
        return state.evolve(responses=(chunk,), tool_calls=(ran,))


class _StubDeliverer:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def deliver_for_job(
        self, job: Job, *, message: str, category: str, urgency: str = "normal"
    ) -> ProactiveDeliveryOutcome:
        self.messages.append(message)
        return ProactiveDeliveryOutcome(rollup="delivered", per_channel={"cli": "delivered"})


def _driver_job() -> Job:
    return Job(
        job_id="objective_driver-seed", handler_name="objective_driver",
        schedule="every 1m", idempotency_key="objective_driver",
        last_run_at=None, next_run_at="2026-06-26T00:00:00+00:00", status="running",
    )


async def _seed(
    db: DbPool, *, criteria: ExpectedOutcome | None,
) -> tuple[ObjectiveStore, str]:
    store = ObjectiveStore(db, DEFAULT_PRINCIPAL_ID)
    objective = Objective(
        objective_id="obj-acc", owner_id=DEFAULT_PRINCIPAL_ID,
        intent="download the report and save it", channel="cli",
    )
    await store.create(objective)
    await store.add_subgoals(
        "obj-acc",
        [SubgoalSpec(description="download the report to a file", acceptance_criteria=criteria)],
    )
    return store, "obj-acc"


async def test_declared_artifact_not_produced_is_failed_not_done(
    db: DbPool, tmp_path: Path,
) -> None:
    """A sub-goal declaring an artifact, whose run produces NO file, is FAILED."""
    produce_dir = tmp_path / "downloads"  # declared, but the run writes nothing here
    store, oid = await _seed(
        db, criteria=ExpectedOutcome(kind="artifact", artifact_dir=str(produce_dir)),
    )
    backend = _ToolEffectBackend(produce_dir=None)  # claims done, writes nothing
    deliverer = _StubDeliverer()
    driver = ObjectiveDriverHandler(db=db, backend=backend, job_deliverer=deliverer)

    await driver.execute(_driver_job())

    subs = await store.list_subgoals(oid)
    assert subs[0].status == "failed", (
        "ACCEPTANCE FAIL: the sub-goal declared an artifact, the run produced no "
        "file, yet it was NOT marked failed — the confident 'All done!' was trusted."
    )
    assert (await store.get(oid)).status == "blocked"
    assert any("stall" in m.lower() or "needs" in m.lower() for m in deliverer.messages), (
        "owner was not honestly notified the objective stalled"
    )
    kinds = [e.kind for e in await store.list_events(oid)]
    assert "subgoal_failed" in kinds


async def test_declared_artifact_produced_is_done(db: DbPool, tmp_path: Path) -> None:
    """When the run actually writes a fresh artifact under the declared dir, the
    sub-goal is ``done`` — acceptance does not false-fail a real success."""
    produce_dir = tmp_path / "downloads"
    store, oid = await _seed(
        db, criteria=ExpectedOutcome(kind="artifact", artifact_dir=str(produce_dir)),
    )
    backend = _ToolEffectBackend(produce_dir=produce_dir)  # writes a real file
    driver = ObjectiveDriverHandler(db=db, backend=backend, job_deliverer=_StubDeliverer())

    await driver.execute(_driver_job())

    subs = await store.list_subgoals(oid)
    assert subs[0].status == "done"


async def test_no_criteria_is_byte_identical_no_error_path(db: DbPool) -> None:
    """A sub-goal with NO acceptance_criteria falls back to the legacy no-error
    path: a clean run is ``done`` regardless of the filesystem (byte-identical)."""
    store, oid = await _seed(db, criteria=None)
    backend = _ToolEffectBackend(produce_dir=None)
    driver = ObjectiveDriverHandler(db=db, backend=backend, job_deliverer=_StubDeliverer())

    await driver.execute(_driver_job())

    subs = await store.list_subgoals(oid)
    assert subs[0].status == "done"


async def test_objective_tool_persists_declared_acceptance_end_to_end(
    db: DbPool,
) -> None:
    """The production entry: a model that marks a download step with
    <<produces-file>> creates an objective whose sub-goal carries an artifact
    acceptance criterion, read back from the store. Proves the declaration flows
    decomposer → store on the real ObjectiveTool path."""
    from stackowl.infra.trace import TraceContext
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.providers.mock_provider import MockProvider
    from stackowl.providers.registry import ProviderRegistry
    from stackowl.tools.scheduling.objective_tool import ObjectiveTool

    pr = ProviderRegistry()
    pr.register_mock(
        "mock-standard",
        MockProvider(
            name="mock-standard",
            canned_text=(
                "fetch the video page\n"
                "download the video to a file <<produces-file: downloads>>\n"
                "notify the owner"
            ),
        ),
        tier="standard",
    )
    token = set_services(StepServices(db_pool=db, provider_registry=pr))
    ttoken = TraceContext.start(session_id="sess-acc", interactive=True, channel="cli")
    try:
        created = await ObjectiveTool().execute(intent="download the video and notify")
    finally:
        TraceContext.reset(ttoken)
        reset_services(token)
    assert created.success

    import json

    oid = json.loads(created.output)["objective_id"]
    subs = await ObjectiveStore(db, DEFAULT_PRINCIPAL_ID).list_subgoals(oid)
    by_desc = {s.description: s for s in subs}
    assert by_desc["fetch the video page"].acceptance_criteria is None
    assert by_desc["notify the owner"].acceptance_criteria is None
    download = by_desc["download the video to a file"].acceptance_criteria
    assert download is not None
    assert download.kind == "artifact" and download.artifact_dir == "downloads"


# ----------------------------------- LLM-derived acceptance (flag-OFF default)


class _DeriveProvider:
    """A standard-tier provider for the post-hoc LLM acceptance deriver. Either
    declares the draft claimed a file under ``artifact_dir`` (``ARTIFACT: dir``),
    or raises to simulate an unavailable model (fail-closed path)."""

    def __init__(self, *, artifact_dir: str | None, raises: bool = False) -> None:
        self.name = "derive-mock"
        self._dir = artifact_dir
        self._raises = raises

    async def complete(self, messages, model, **kwargs):  # type: ignore[no-untyped-def]
        if self._raises:
            raise RuntimeError("model unavailable")
        from stackowl.providers.base import CompletionResult

        body = f"ARTIFACT: {self._dir}" if self._dir is not None else "NONE"
        return CompletionResult(
            content=body, input_tokens=1, output_tokens=1,
            model="derive-mock", provider_name="derive-mock", duration_ms=0.0,
        )


def _registry_with(provider: _DeriveProvider):  # type: ignore[no-untyped-def]
    from stackowl.providers.registry import ProviderRegistry

    pr = ProviderRegistry()
    pr.register_mock(provider.name, provider, tier="standard")
    return pr


def _settings_with_acceptance_tier(tier: str):  # type: ignore[no-untyped-def]
    from stackowl.config.settings import Settings

    return Settings().model_copy(update={"acceptance_tier": tier})


async def test_llm_layer_off_by_default_is_byte_identical(db: DbPool) -> None:
    """With acceptance_tier unset (default OFF), a sub-goal with NO declared
    criterion and no artifact is still ``done`` — the LLM layer never engages."""
    store, oid = await _seed(db, criteria=None)
    driver = ObjectiveDriverHandler(
        db=db, backend=_ToolEffectBackend(produce_dir=None),
        settings=_settings_with_acceptance_tier(""),  # OFF
        provider_registry=_registry_with(_DeriveProvider(artifact_dir="downloads")),
        job_deliverer=_StubDeliverer(),
    )
    await driver.execute(_driver_job())
    assert (await store.list_subgoals(oid))[0].status == "done"


async def test_llm_layer_on_catches_undeclared_claimed_artifact(
    db: DbPool, tmp_path: Path,
) -> None:
    """Flag ON: a sub-goal with NO declared criterion whose draft the model says
    claimed a saved file — but none exists — is marked ``failed`` post-hoc."""
    store, oid = await _seed(db, criteria=None)
    driver = ObjectiveDriverHandler(
        db=db, backend=_ToolEffectBackend(produce_dir=None),
        settings=_settings_with_acceptance_tier("standard"),  # ON
        provider_registry=_registry_with(
            _DeriveProvider(artifact_dir=str(tmp_path / "nope")),
        ),
        job_deliverer=_StubDeliverer(),
    )
    await driver.execute(_driver_job())
    assert (await store.list_subgoals(oid))[0].status == "failed"


async def test_llm_layer_fail_closed_when_model_unavailable(db: DbPool) -> None:
    """Flag ON but the model is unavailable: the deriver asserts NO positive
    acceptance and NO false failure — the sub-goal falls back to the legacy
    no-error path (honest-limit, never a silent pass or a fabricated failure)."""
    store, oid = await _seed(db, criteria=None)
    driver = ObjectiveDriverHandler(
        db=db, backend=_ToolEffectBackend(produce_dir=None),
        settings=_settings_with_acceptance_tier("standard"),  # ON
        provider_registry=_registry_with(
            _DeriveProvider(artifact_dir=None, raises=True),
        ),
        job_deliverer=_StubDeliverer(),
    )
    await driver.execute(_driver_job())
    assert (await store.list_subgoals(oid))[0].status == "done"
