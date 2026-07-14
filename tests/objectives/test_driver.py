"""ObjectiveDriverHandler — advance standing objectives one sub-goal per tick (1C).

The driver is the functional heart of the keystone: when the scheduler fires it,
it advances each active objective by its next pending sub-goal (run through the
pipeline backend), records progress, and decides continue / done / blocked. The
act-on-reversible posture means an autonomous sub-goal that parks (a consequential
action it cannot get consent for, non-interactively) blocks the objective and
pings the owner — it never silently acts on the irreversible thing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.notifications.proactive_job import ProactiveDeliveryOutcome
from stackowl.objectives.driver import (
    _ADAPTIVE_DECOMPOSITION_THRESHOLD,
    _MAX_DECOMPOSITION_DEPTH,
    ObjectiveDriverHandler,
)
from stackowl.objectives.model import Objective, SubgoalSpec
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "driver.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


class _FakeBackend:
    """Echoes the input as a single response; configurable errors / park.

    ``consequential`` populates ``PipelineState.consequential_failures`` so a park can
    be classified irreversible (touched a consequential tool) vs reversible (none)."""

    def __init__(
        self,
        *,
        text: str = "did it",
        errors: tuple[str, ...] = (),
        parked: bool = False,
        consequential: tuple[str, ...] = (),
    ) -> None:
        self._text = text
        self._errors = errors
        self._parked = parked
        self._consequential = consequential
        self.runs = 0

    async def run(self, state: PipelineState) -> PipelineState:
        self.runs += 1
        chunk = ResponseChunk(
            content=self._text, is_final=False, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        )
        return state.evolve(
            responses=(chunk,), errors=self._errors, durable_parked=self._parked,
            consequential_failures=self._consequential,
        )


class _FakeDeliverer:
    """Records every deliver_for_job call; always reports delivered."""

    def __init__(self) -> None:
        self.calls: list[tuple[Job, str, str]] = []

    async def deliver_for_job(
        self, job: Job, *, message: str, category: str, urgency: str = "normal",
    ) -> ProactiveDeliveryOutcome:
        self.calls.append((job, message, category))
        return ProactiveDeliveryOutcome(rollup="delivered", per_channel={"telegram": "delivered"})


class _RecordingBackend:
    """Records each run's input_text; fails the first ``fail_times`` runs then succeeds.

    Lets a test observe WHAT the driver fed into the sub-goal on each (re)try — used to
    prove a retry carries the prior failure context rather than running cold (F-43)."""

    def __init__(self, *, fail_times: int, error: str = "boom") -> None:
        self._fail_times = fail_times
        self._error = error
        self.inputs: list[str] = []
        self.runs = 0

    async def run(self, state: PipelineState) -> PipelineState:
        self.inputs.append(state.input_text)
        self.runs += 1
        errors = (self._error,) if self.runs <= self._fail_times else ()
        chunk = ResponseChunk(
            content="ok", is_final=False, chunk_index=0,
            trace_id=state.trace_id, owl_name=state.owl_name,
        )
        return state.evolve(responses=(chunk,), errors=errors)


def _driver_job() -> Job:
    return Job(
        job_id="objective_driver-seed",
        handler_name="objective_driver",
        schedule="every 1m",
        idempotency_key="objective_driver",
        last_run_at=None,
        next_run_at="2026-06-24T00:00:00+00:00",
        status="running",
    )


async def _make_objective(
    store: ObjectiveStore, subgoals: list[str], *, objective_id: str = "obj-1",
) -> Objective:
    obj = Objective(
        objective_id=objective_id,
        owner_id="principal-default",
        intent="watch X and handle it",
        channel="telegram",
        target_channels=["telegram"],
        target_addresses={"telegram": 999},
    )
    await store.create(obj)
    await store.add_subgoals(objective_id, subgoals)
    await store.append_event(objective_id, "created", "objective created")
    return obj


def test_handler_name_and_trigger_kind() -> None:
    h = ObjectiveDriverHandler(db=None, backend=None)
    assert h.handler_name == "objective_driver"
    assert h.trigger_kind == "seeded"


async def test_advances_one_pending_subgoal_per_tick(pool: DbPool) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["step a", "step b"])
    backend = _FakeBackend(text="answer a")
    handler = ObjectiveDriverHandler(db=pool, backend=backend)

    result = await handler.execute(_driver_job())

    assert result.success
    assert backend.runs == 1  # exactly one sub-goal advanced this tick
    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "done" and subs[0].result == "answer a"
    assert subs[1].status == "pending"
    kinds = [e.kind for e in await store.list_events("obj-1")]
    assert "subgoal_done" in kinds


async def test_completes_objective_and_notifies_when_all_done(pool: DbPool) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["only step"])
    backend = _FakeBackend(text="finished")
    deliverer = _FakeDeliverer()
    handler = ObjectiveDriverHandler(db=pool, backend=backend, job_deliverer=deliverer)

    await handler.execute(_driver_job())   # tick 1 — run the sub-goal
    await handler.execute(_driver_job())   # tick 2 — no pending → complete

    obj = await store.get("obj-1")
    assert obj.status == "done"
    assert len(deliverer.calls) == 1  # notified once, on completion
    _job, message, _category = deliverer.calls[0]
    assert "watch X and handle it" in message


async def test_blocks_objective_when_irreversible_subgoal_parks(pool: DbPool) -> None:
    """F-44: a park that touched a CONSEQUENTIAL tool is a genuine ask-on-irreversible
    decision — block the objective (blocker_kind=decision) and ping the owner."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["risky step"])
    backend = _FakeBackend(
        parked=True,
        errors=("needs consent for an irreversible action",),
        consequential=("send_email",),  # a consequential tool was attempted → irreversible
    )
    deliverer = _FakeDeliverer()
    handler = ObjectiveDriverHandler(db=pool, backend=backend, job_deliverer=deliverer)

    await handler.execute(_driver_job())

    obj = await store.get("obj-1")
    assert obj.status == "blocked"
    assert obj.blocker and "irreversible" in obj.blocker
    assert obj.blocker_kind == "decision"  # needs a human — never auto-requeued
    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "blocked"
    assert len(deliverer.calls) == 1  # owner pinged about the block


async def test_reversible_park_auto_resolves_without_blocking(pool: DbPool) -> None:
    """F-44: a park with NO consequential footprint is a trivial/reversible clarify the
    assistant may resolve itself — it must NOT strand the whole objective. It defers to
    the bounded-retry path (act-first next tick) and never pings the owner prematurely."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["mild clarify step"])
    backend = _FakeBackend(parked=True, errors=("which colour do you prefer?",))
    deliverer = _FakeDeliverer()
    handler = ObjectiveDriverHandler(db=pool, backend=backend, job_deliverer=deliverer)

    await handler.execute(_driver_job())

    obj = await store.get("obj-1")
    assert obj.status == "active"  # NOT blocked on a reversible clarify
    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "pending"  # re-queued for a best-effort retry
    assert subs[0].attempts == 1
    assert len(deliverer.calls) == 0  # owner NOT pinged for a trivial decision


async def test_transient_blocked_objective_requeued_after_cooldown(pool: DbPool) -> None:
    """F-41: a transient-blocked objective is recoverable — once the cooldown elapses the
    driver resets the stuck sub-goal's budget and returns the objective to ``active``."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["doomed step"])
    backend = _FakeBackend(errors=("boom",))
    deliverer = _FakeDeliverer()
    # cooldown=0 → an objective blocked this tick is immediately eligible next tick.
    handler = ObjectiveDriverHandler(
        db=pool, backend=backend, job_deliverer=deliverer, blocked_retry_cooldown_s=0.0,
    )

    # Exhaust the retry budget → transient block.
    await handler.execute(_driver_job())  # attempt 1
    await handler.execute(_driver_job())  # attempt 2
    await handler.execute(_driver_job())  # attempt 3 → blocked (transient)
    obj = await store.get("obj-1")
    assert obj.status == "blocked"
    assert obj.blocker_kind == "transient"

    # Next tick: cooldown elapsed → re-queued to active AND advanced again same tick.
    await handler.execute(_driver_job())
    obj = await store.get("obj-1")
    assert obj.status == "active"  # recovered, no longer abandoned
    subs = await store.list_subgoals("obj-1")
    # The stuck sub-goal was reset to a fresh budget then re-attempted once this tick.
    assert subs[0].attempts == 1
    kinds = [e.kind for e in await store.list_events("obj-1")]
    assert "requeued" in kinds


async def test_decision_blocked_objective_is_not_requeued(pool: DbPool) -> None:
    """F-41: a decision-class block (irreversible / verified-false) is NEVER auto-requeued
    — it waits for a human even after the cooldown would otherwise elapse."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["risky step"])
    backend = _FakeBackend(
        parked=True, errors=("irreversible action",), consequential=("send_email",),
    )
    handler = ObjectiveDriverHandler(
        db=pool, backend=backend, blocked_retry_cooldown_s=0.0,
    )

    await handler.execute(_driver_job())  # → decision block
    runs_after_block = backend.runs
    await handler.execute(_driver_job())  # cooldown=0 but decision must NOT recover

    obj = await store.get("obj-1")
    assert obj.status == "blocked"
    assert obj.blocker_kind == "decision"
    assert backend.runs == runs_after_block  # the step was NOT re-attempted


async def test_retries_subgoal_on_transient_error_before_blocking(pool: DbPool) -> None:
    """F-40: a single sub-goal error must NOT permanently strand the objective.

    The first failure leaves the sub-goal ``pending`` (attempts bumped, objective
    still ``active``, no owner ping) so the next tick retries it. Only after the
    retry budget is exhausted does the objective escalate to ``blocked``."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["flaky step"])
    backend = _FakeBackend(errors=("boom",))
    deliverer = _FakeDeliverer()
    handler = ObjectiveDriverHandler(db=pool, backend=backend, job_deliverer=deliverer)

    # Tick 1 — first failure: retried, not blocked.
    await handler.execute(_driver_job())
    obj = await store.get("obj-1")
    assert obj.status == "active"  # objective NOT stranded on the first stumble
    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "pending"  # re-queued for the next tick
    assert subs[0].attempts == 1
    assert len(deliverer.calls) == 0  # no premature owner ping

    # Tick 2 — second failure: still under budget (MAX=3), retried again.
    await handler.execute(_driver_job())
    obj = await store.get("obj-1")
    assert obj.status == "active"
    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "pending"
    assert subs[0].attempts == 2
    assert len(deliverer.calls) == 0


async def test_blocks_objective_after_retry_budget_exhausted(pool: DbPool) -> None:
    """F-40: once the bounded retry budget is spent, escalate to ``blocked`` + notify."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["doomed step"])
    backend = _FakeBackend(errors=("boom",))
    deliverer = _FakeDeliverer()
    handler = ObjectiveDriverHandler(db=pool, backend=backend, job_deliverer=deliverer)

    # MAX_SUBGOAL_ATTEMPTS == 3 → tick 1 & 2 retry, tick 3 exhausts the budget.
    await handler.execute(_driver_job())  # attempt 1 → pending
    await handler.execute(_driver_job())  # attempt 2 → pending
    await handler.execute(_driver_job())  # attempt 3 → blocked

    obj = await store.get("obj-1")
    assert obj.status == "blocked"
    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "failed"
    assert subs[0].attempts == 3
    assert len(deliverer.calls) == 1  # owner pinged once, on the terminal escalation
    assert backend.runs == 3  # the step was genuinely retried each tick


async def test_completed_subgoal_without_criteria_is_unverified(pool: DbPool) -> None:
    """F-42: with NO declared acceptance criterion (and the LLM deriver off), a clean
    run completes the sub-goal but records verified=False — completion is honest, not
    over-claimed as a verified success."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["just answer"])
    backend = _FakeBackend(text="here you go")
    handler = ObjectiveDriverHandler(db=pool, backend=backend)

    await handler.execute(_driver_job())

    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "done"
    assert subs[0].verified is False  # completed but UNVERIFIED — no criterion to check


async def test_retry_feeds_prior_failure_into_subgoal_context(pool: DbPool) -> None:
    """F-43: a retry must not run COLD. The first attempt runs the bare description; a
    subsequent retry of the same (previously-failed) sub-goal carries the prior failure
    reason into its run so the backend can choose a different approach. This is
    operational within-turn context, NOT persisted negative learning."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["fetch the data"])
    backend = _RecordingBackend(fail_times=1, error="connection refused")
    handler = ObjectiveDriverHandler(db=pool, backend=backend)

    # Tick 1 — first attempt runs the bare description (cold), then fails → re-queued.
    await handler.execute(_driver_job())
    assert backend.inputs[0] == "fetch the data"  # first attempt is byte-identical
    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "pending" and subs[0].attempts == 1

    # Tick 2 — the retry feeds the prior failure back in.
    await handler.execute(_driver_job())
    assert len(backend.inputs) == 2
    assert "fetch the data" in backend.inputs[1]          # original intent preserved
    assert "connection refused" in backend.inputs[1]      # prior failure surfaced
    assert backend.inputs[1] != backend.inputs[0]         # NOT run cold again
    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "done"                        # different run now succeeded


async def test_first_attempt_runs_cold_without_retry_note(pool: DbPool) -> None:
    """F-43: a fresh sub-goal with no prior result is run with its bare description —
    the prior-failure note only appears on an actual retry (byte-identical first run)."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["just answer"])
    backend = _RecordingBackend(fail_times=0)
    handler = ObjectiveDriverHandler(db=pool, backend=backend)

    await handler.execute(_driver_job())

    assert backend.inputs == ["just answer"]  # no retry note on the first, clean attempt


async def test_no_active_objectives_is_noop_success(pool: DbPool) -> None:
    backend = _FakeBackend()
    handler = ObjectiveDriverHandler(db=pool, backend=backend)
    result = await handler.execute(_driver_job())
    assert result.success
    assert backend.runs == 0


async def test_notify_targets_the_objective_recipient(pool: DbPool) -> None:
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["only step"])
    backend = _FakeBackend()
    deliverer = _FakeDeliverer()
    handler = ObjectiveDriverHandler(db=pool, backend=backend, job_deliverer=deliverer)

    await handler.execute(_driver_job())  # run sub-goal
    await handler.execute(_driver_job())  # complete + notify

    job, _message, _category = deliverer.calls[0]
    # The synthetic delivery job carries the OBJECTIVE's durable recipient, not
    # the driver job's (which has none).
    assert job.target_channels == ["telegram"]
    assert job.target_addresses == {"telegram": 999}


# --- ADR-2: the retry decision delegates to the one RecoveryActuator ----------


class _SpyActuator:
    """A RecoveryActuator stand-in that records the Failures it was asked to classify.

    Lets a test prove the objective driver routes its retry-vs-escalate DECISION through
    the one authority (``should_retry``) rather than an inline guard. Delegates to the real
    predicate so behavior stays byte-identical."""

    def __init__(self) -> None:
        from stackowl.pipeline.recovery_actuator import RecoveryActuator

        self._real = RecoveryActuator()
        self.calls: list[object] = []

    def should_retry(self, failure: object) -> bool:
        self.calls.append(failure)
        return self._real.should_retry(failure)  # type: ignore[arg-type]


def _settings_with(**overrides: object):
    """Build a Settings with overrides (kwargs are silently dropped by the customised
    sources — model_copy is the only honoured path; see memory)."""
    from stackowl.config.settings import Settings

    return Settings().model_copy(update=overrides)


async def test_retry_decision_routes_through_actuator_when_unify_on(pool: DbPool) -> None:
    """ADR-2: with ``unify_objective_recovery`` ON (default), a sub-goal failure's
    retry decision is made by the RecoveryActuator — it is consulted with a typed,
    non-consequential ``Failure`` — and the behavior is byte-identical (retried)."""
    from stackowl.pipeline.recovery_actuator import Failure

    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["flaky step"])
    backend = _FakeBackend(errors=("boom",))
    spy = _SpyActuator()
    handler = ObjectiveDriverHandler(db=pool, backend=backend, recovery=spy)  # type: ignore[arg-type]

    await handler.execute(_driver_job())

    # The authority was consulted with a typed objective Failure (delegation).
    assert len(spy.calls) == 1
    failure = spy.calls[0]
    assert isinstance(failure, Failure)
    assert failure.kind == "objective"
    assert failure.consequential is False
    # Byte-identical outcome: retried, not stranded.
    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "pending" and subs[0].attempts == 1


async def test_retry_decision_inline_when_unify_off(pool: DbPool) -> None:
    """Flag OFF ⇒ the inline attempt-budget decision is used; the actuator is NOT
    consulted, and behavior is the same byte-identical retry (regression-safe)."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["flaky step"])
    backend = _FakeBackend(errors=("boom",))
    spy = _SpyActuator()
    handler = ObjectiveDriverHandler(
        db=pool, backend=backend, recovery=spy,  # type: ignore[arg-type]
        settings=_settings_with(unify_objective_recovery=False),
    )

    await handler.execute(_driver_job())

    assert spy.calls == []  # inline path — authority not consulted
    subs = await store.list_subgoals("obj-1")
    assert subs[0].status == "pending" and subs[0].attempts == 1


# --------------------------------------- Task 3: adaptive decomposition ------


def _standard_tier_registry(canned_text: str) -> ProviderRegistry:
    """A ProviderRegistry with a mock decomposer provider on the 'standard' tier
    (the tier ObjectiveDecomposer always uses)."""
    registry = ProviderRegistry()
    registry.register_mock(
        "mock-standard", MockProvider(name="mock-standard", canned_text=canned_text),
        tier="standard",
    )
    return registry


async def _seed_objective_with_spec(
    store: ObjectiveStore, spec: SubgoalSpec, *, objective_id: str = "obj-1",
) -> Objective:
    """Like _make_objective but for a single sub-goal carrying a specific
    estimated_complexity (Task 3 tests need to control this directly)."""
    obj = Objective(
        objective_id=objective_id, owner_id="principal-default",
        intent="a complex objective", channel="telegram",
        target_channels=["telegram"], target_addresses={"telegram": 999},
    )
    await store.create(obj)
    await store.add_subgoals(objective_id, [spec])
    await store.append_event(objective_id, "created", "objective created")
    return obj


async def test_low_complexity_subgoal_does_not_recurse(pool: DbPool) -> None:
    """A sub-goal below the adaptive-decomposition threshold runs as a single
    step, exactly as before Task 3 — even with a decomposer-capable provider
    registry wired."""
    store = ObjectiveStore(pool, "principal-default")
    await _seed_objective_with_spec(
        store,
        SubgoalSpec(
            description="a simple step",
            estimated_complexity=_ADAPTIVE_DECOMPOSITION_THRESHOLD - 0.1,
        ),
    )
    registry = _standard_tier_registry("child a\nchild b")
    backend = _FakeBackend(text="did it")
    handler = ObjectiveDriverHandler(db=pool, backend=backend, provider_registry=registry)

    await handler.execute(_driver_job())

    subs = await store.list_subgoals("obj-1")
    assert len(subs) == 1  # never split
    assert subs[0].status == "done" and subs[0].result == "did it"
    assert backend.runs == 1


async def test_high_complexity_subgoal_runs_as_is_without_provider_registry(
    pool: DbPool,
) -> None:
    """No provider registry wired ⇒ _maybe_decompose_further has no decomposer
    to call at all — a high-complexity sub-goal must still run as-is (fail-safe)
    rather than getting stuck forever waiting on a split that can never happen."""
    store = ObjectiveStore(pool, "principal-default")
    await _seed_objective_with_spec(
        store,
        SubgoalSpec(description="a big thing", estimated_complexity=1.0),
    )
    backend = _FakeBackend(text="ran anyway")
    handler = ObjectiveDriverHandler(db=pool, backend=backend)  # no provider_registry

    await handler.execute(_driver_job())

    subs = await store.list_subgoals("obj-1")
    assert len(subs) == 1  # never split — no decomposer available
    assert subs[0].status == "done" and subs[0].result == "ran anyway"
    assert backend.runs == 1


async def test_high_complexity_subgoal_recurses_exactly_once(pool: DbPool) -> None:
    """A sub-goal AT/ABOVE the threshold is split one level deeper before it
    runs; its (low-complexity) children then run normally with no further
    recursion, and the split happened exactly once."""
    store = ObjectiveStore(pool, "principal-default")
    await _seed_objective_with_spec(
        store,
        SubgoalSpec(
            description="do a big multi-part thing",
            estimated_complexity=_ADAPTIVE_DECOMPOSITION_THRESHOLD + 0.1,
        ),
    )
    # Children carry NO complexity marker → default 0.0 → never recurse further.
    registry = _standard_tier_registry("first half\nsecond half")
    backend = _FakeBackend(text="done")
    handler = ObjectiveDriverHandler(db=pool, backend=backend, provider_registry=registry)

    await handler.execute(_driver_job())  # tick 1: planning-only — splits, runs nothing

    assert backend.runs == 0
    subs = await store.list_subgoals("obj-1")
    assert [s.description for s in subs] == ["first half", "second half"]
    assert all(s.decomposition_depth == 1 for s in subs)
    assert all(s.status == "pending" for s in subs)

    await handler.execute(_driver_job())  # tick 2: run first child
    await handler.execute(_driver_job())  # tick 3: run second child
    assert backend.runs == 2

    kinds = [e.kind for e in await store.list_events("obj-1")]
    assert kinds.count("subgoal_decomposed") == 1  # split exactly once


async def test_recursion_never_exceeds_depth_cap(pool: DbPool) -> None:
    """Even when every generated child is ALSO reported as highly complex, the
    driver never splits past _MAX_DECOMPOSITION_DEPTH — it eventually runs a
    sub-goal at the cap instead of decomposing it again, so the objective always
    makes forward progress."""
    store = ObjectiveStore(pool, "principal-default")
    await _seed_objective_with_spec(
        store,
        SubgoalSpec(
            description="perpetually complex step",
            estimated_complexity=1.0,
        ),
    )
    # Every decomposition call (regardless of input) returns 2 equally "complex"
    # children — the decomposer would keep wanting to split forever if the depth
    # cap did not intervene.
    registry = _standard_tier_registry(
        "part one <<complexity: 1.0>>\npart two <<complexity: 1.0>>"
    )
    backend = _FakeBackend(text="ran")
    handler = ObjectiveDriverHandler(db=pool, backend=backend, provider_registry=registry)

    for _ in range(12):  # generous bound — must terminate long before this
        await handler.execute(_driver_job())

    subs = await store.list_subgoals("obj-1")
    assert all(s.decomposition_depth <= _MAX_DECOMPOSITION_DEPTH for s in subs)
    # At least one sub-goal actually ran (the cap forced execution, not endless
    # splitting) — real forward progress, not a stuck loop.
    assert backend.runs > 0
    assert any(s.status == "done" for s in subs)


# ------------------------------------------------ Task 3: recombination -----


def _powerful_tier_registry(canned_text: str) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register_mock(
        "mock-powerful", MockProvider(name="mock-powerful", canned_text=canned_text),
        tier="powerful",
    )
    return registry


async def test_completion_synthesizes_combined_answer_not_echoed_intent(
    pool: DbPool,
) -> None:
    """On completion of a multi-step objective, the notified message contains a
    real synthesis over the sub-goals' results — not just the bare, verbatim
    original-intent string the legacy behavior sent."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["gather prices", "compare options"])
    backend = _FakeBackend(text="step result")
    deliverer = _FakeDeliverer()
    registry = _powerful_tier_registry("The cheapest option overall is Plan B.")
    handler = ObjectiveDriverHandler(
        db=pool, backend=backend, job_deliverer=deliverer, provider_registry=registry,
    )

    await handler.execute(_driver_job())  # step 1
    await handler.execute(_driver_job())  # step 2
    await handler.execute(_driver_job())  # completion tick

    obj = await store.get("obj-1")
    assert obj.status == "done"
    _job, message, _category = deliverer.calls[-1]
    assert "The cheapest option overall is Plan B." in message
    # Not the old bare echo — the synthesized content is present alongside it.
    legacy_echo = f"✓ Objective complete: {obj.intent}"
    assert message != legacy_echo


async def test_completion_surfaces_degraded_synthesis_provider(
    pool: DbPool, caplog: pytest.LogCaptureFixture,
) -> None:
    """When no 'powerful' provider is registered, resolve_capable_or_degrade
    substitutes the most-capable available tier and reports degraded_from — the
    driver must SURFACE that (mirrors ParliamentSynthesizer.synthesize, F125),
    never present a degraded-tier synthesis identically to a genuine one."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["gather prices", "compare options"])
    backend = _FakeBackend(text="step result")
    deliverer = _FakeDeliverer()
    # Only a 'standard'-tier mock is registered — no 'powerful' provider exists,
    # so resolving 'powerful' must degrade to it.
    registry = _standard_tier_registry("Plan B wins on price.")
    handler = ObjectiveDriverHandler(
        db=pool, backend=backend, job_deliverer=deliverer, provider_registry=registry,
    )

    with caplog.at_level("WARNING", logger="stackowl.scheduler"):
        await handler.execute(_driver_job())  # step 1
        await handler.execute(_driver_job())  # step 2
        await handler.execute(_driver_job())  # completion tick

    _job, message, _category = deliverer.calls[-1]
    assert "Plan B wins on price." in message
    assert "less-capable substitute" in message  # visible degrade note
    assert any(
        "DEGRADED" in r.message or "degraded" in r.message.lower()
        for r in caplog.records
    )


async def test_completion_falls_back_when_no_provider_registry(pool: DbPool) -> None:
    """No provider registry wired (legacy/unit surface) ⇒ degrade to a
    concatenation of step results rather than raising or reintroducing the bare
    echoed-intent-only message."""
    store = ObjectiveStore(pool, "principal-default")
    await _make_objective(store, ["step a", "step b"])
    backend = _FakeBackend(text="the real output")
    deliverer = _FakeDeliverer()
    handler = ObjectiveDriverHandler(db=pool, backend=backend, job_deliverer=deliverer)

    await handler.execute(_driver_job())
    await handler.execute(_driver_job())
    await handler.execute(_driver_job())

    _job, message, _category = deliverer.calls[-1]
    assert "the real output" in message  # a real sub-goal result, not just the intent


# ------------------------------------------------- Task 8: epic branch -----


def _epic_objective(objective_id: str) -> Objective:
    return Objective(
        objective_id=objective_id, owner_id="principal-default", intent="epic",
        repo="/tmp/fake-repo", integration_branch=f"stackowl/epic-{objective_id}",
        base_branch="main",
    )


async def test_epic_advance_launches_ready_stories_concurrently(
    pool: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task #4 epic path: independent stories are launched CONCURRENTLY in one
    tick, not one-per-tick like the plain-objective linear path."""
    store = ObjectiveStore(pool, "principal-default")
    objective = _epic_objective("obj-epic")
    await store.create(objective)
    await store.add_subgoals(
        "obj-epic",
        [SubgoalSpec(description="a"), SubgoalSpec(description="b")],  # both independent
    )

    launched: list[str] = []

    async def _fake_run_story(
        objective: Objective, subgoal: object, store_: ObjectiveStore, locks: object,
    ) -> None:
        launched.append(subgoal.subgoal_id)  # type: ignore[attr-defined]
        await store_.update_subgoal(subgoal.subgoal_id, "done", result="ok")  # type: ignore[attr-defined]

    monkeypatch.setattr("stackowl.objectives.driver.run_story", _fake_run_story)
    handler = ObjectiveDriverHandler(db=pool, backend=None)

    result = await handler._advance(store, await store.get("obj-epic"))

    assert result is True
    for _ in range(20):
        if len(launched) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(launched) == 2  # both independent stories launched THIS tick


async def test_epic_failure_isolation_does_not_block_siblings(
    pool: DbPool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One story blocking must not prevent an independent sibling from completing."""
    store = ObjectiveStore(pool, "principal-default")
    objective = _epic_objective("obj-epic2")
    await store.create(objective)
    await store.add_subgoals(
        "obj-epic2",
        [SubgoalSpec(description="a"), SubgoalSpec(description="b")],  # independent
    )

    async def _fake_run_story(
        objective: Objective, subgoal: object, store_: ObjectiveStore, locks: object,
    ) -> None:
        if subgoal.description == "a":  # type: ignore[attr-defined]
            await store_.update_subgoal(subgoal.subgoal_id, "blocked", result="failed")  # type: ignore[attr-defined]
        else:
            await store_.update_subgoal(subgoal.subgoal_id, "done", result="ok")  # type: ignore[attr-defined]

    monkeypatch.setattr("stackowl.objectives.driver.run_story", _fake_run_story)
    handler = ObjectiveDriverHandler(db=pool, backend=None)

    await handler._advance(store, await store.get("obj-epic2"))

    for _ in range(20):
        subgoals = await store.list_subgoals("obj-epic2")
        if all(sg.status in ("blocked", "done") for sg in subgoals):
            break
        await asyncio.sleep(0.01)
    subgoals = await store.list_subgoals("obj-epic2")
    by_desc = {sg.description: sg for sg in subgoals}
    assert by_desc["a"].status == "blocked"
    assert by_desc["b"].status == "done"
