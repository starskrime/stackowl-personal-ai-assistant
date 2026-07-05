"""WS-E — the STARTUP WIRING-CLOSURE audit.

The platform validated that scheduler handlers were REGISTERED but never that
they were REACHABLE. Three production bugs (check_in seeded-but-never, an
event_bridge subscriber with no publisher, goal_execution registered with a
dangling delivery half) all shipped green because nothing checked the closure
of the wiring graph. ``audit_scheduler_wiring`` is that check: it flags a
registered "seeded"-kind handler with no standing ``jobs`` row (it will never
fire) and a subscribed event with no declared publisher (a dangling half-edge).

These tests drive the audit directly with a real in-memory ``jobs`` table and a
real :class:`HandlerRegistry`, and the regression test pins the EXPECTED dangling
set of the real assembled registry so a NEW accidental dangling handler fails.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult
from stackowl.startup.wiring_audit import (
    WiringReport,
    audit_owl_wiring,
    audit_scheduler_wiring,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- helpers


class _FakeHandler(JobHandler):
    """Minimal handler with a configurable name + trigger_kind."""

    def __init__(self, name: str, trigger_kind: str = "seeded") -> None:
        self._name = name
        self._trigger_kind = trigger_kind

    @property
    def handler_name(self) -> str:
        return self._name

    @property
    def trigger_kind(self) -> str:  # type: ignore[override]
        return self._trigger_kind

    async def execute(self, job: Job) -> JobResult:  # pragma: no cover — never run
        return JobResult(job_id=job.job_id, success=True, output=None, error=None, duration_ms=0.0)


@pytest.fixture(autouse=True)
def _reset_registry() -> object:
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


async def _seed_job(db: DbPool, handler_name: str) -> None:
    """Insert a minimal standing jobs row for ``handler_name``."""
    await db.execute(
        "INSERT INTO jobs (job_id, handler_name, schedule, idempotency_key, "
        "next_run_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            f"{handler_name}-1",
            handler_name,
            "daily@08:00",
            f"{handler_name}-key",
            "2026-06-19T08:00:00+00:00",
            "2026-06-19T00:00:00+00:00",
        ),
    )


# --------------------------------------------------------------------------- tests


async def test_seeded_handler_with_row_is_not_dangling(tmp_db: DbPool) -> None:
    reg = HandlerRegistry.instance()
    reg.register(_FakeHandler("morning_brief", "seeded"))
    await _seed_job(tmp_db, "morning_brief")

    report = await audit_scheduler_wiring(
        tmp_db, reg, allowed_events=frozenset(), declared_publishers=set()
    )

    assert isinstance(report, WiringReport)
    assert report.dangling_handlers == []
    assert report.total_handlers == 1
    assert report.seeded == 1


async def test_seeded_handler_without_row_is_dangling_and_warns(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture
) -> None:
    reg = HandlerRegistry.instance()
    reg.register(_FakeHandler("ghost_handler", "seeded"))
    # No jobs row seeded — this handler will never fire.

    with caplog.at_level(logging.WARNING):
        report = await audit_scheduler_wiring(
            tmp_db, reg, allowed_events=frozenset(), declared_publishers=set()
        )

    assert report.dangling_handlers == ["ghost_handler"]
    # A WARNING that NAMES the dangling handler must be logged.
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("ghost_handler" in m for m in warnings)


async def test_on_demand_handler_without_row_is_not_dangling(tmp_db: DbPool) -> None:
    reg = HandlerRegistry.instance()
    reg.register(_FakeHandler("goal_execution", "on_demand"))
    # No jobs row — expected: on_demand handlers have no standing seed.

    report = await audit_scheduler_wiring(
        tmp_db, reg, allowed_events=frozenset(), declared_publishers=set()
    )

    assert report.dangling_handlers == []
    assert report.on_demand == 1


async def test_event_without_publisher_is_dangling(tmp_db: DbPool) -> None:
    reg = HandlerRegistry.instance()
    report = await audit_scheduler_wiring(
        tmp_db,
        reg,
        allowed_events=frozenset({"some.subscribed.event"}),
        declared_publishers=set(),
    )
    assert report.dangling_events == ["some.subscribed.event"]


async def test_event_with_publisher_is_not_dangling(tmp_db: DbPool) -> None:
    reg = HandlerRegistry.instance()
    report = await audit_scheduler_wiring(
        tmp_db,
        reg,
        allowed_events=frozenset({"some.subscribed.event"}),
        declared_publishers={"some.subscribed.event"},
    )
    assert report.dangling_events == []


async def test_consolidated_info_summary_is_emitted(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture
) -> None:
    reg = HandlerRegistry.instance()
    reg.register(_FakeHandler("morning_brief", "seeded"))
    reg.register(_FakeHandler("goal_execution", "on_demand"))
    await _seed_job(tmp_db, "morning_brief")

    with caplog.at_level(logging.INFO):
        await audit_scheduler_wiring(
            tmp_db, reg, allowed_events=frozenset(), declared_publishers=set()
        )

    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("wiring audit" in m for m in infos)


async def test_audit_never_raises_when_db_query_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A degraded audit (db unavailable) must NOT block startup — never raises."""

    class _BrokenDb:
        async def fetch_all(self, *args: object, **kwargs: object) -> list[dict[str, object]]:
            raise RuntimeError("db is down")

    reg = HandlerRegistry.instance()
    reg.register(_FakeHandler("morning_brief", "seeded"))

    with caplog.at_level(logging.WARNING):
        report = await audit_scheduler_wiring(
            _BrokenDb(),  # type: ignore[arg-type]
            reg,
            allowed_events=frozenset(),
            declared_publishers=set(),
        )
    # Degraded but did not raise. With no seeded set known, it does not assert
    # dangling (cannot prove unreachability when the query failed).
    assert isinstance(report, WiringReport)


async def test_real_registry_has_no_unexpected_dangling_handlers(
    tmp_db: DbPool, tmp_path: object,
) -> None:
    """Regression: the REAL assembled registry has no UNEXPECTED dangling handler.

    This is the test that would have caught the check_in bug: every "seeded"
    handler must actually be seeded. The EXPECTED dangling set is pinned
    explicitly — under default test settings ``check_in`` is disabled so its
    row is (correctly) absent, which is honest signal, not a regression. A NEW
    accidental dangling handler (a seeded-kind handler nobody seeds) fails here.
    """
    from tests.startup._wiring_real_assembly import build_real_scheduler

    reg = await build_real_scheduler(tmp_db, tmp_path)  # type: ignore[arg-type]

    report = await audit_scheduler_wiring(
        tmp_db, reg, allowed_events=frozenset(), declared_publishers=set()
    )

    # check_in is disabled by default → its seed is intentionally absent.
    EXPECTED_DANGLING = {"check_in"}
    unexpected = set(report.dangling_handlers) - EXPECTED_DANGLING
    assert unexpected == set(), (
        f"NEW dangling seeded handler(s) registered but never seeded: {unexpected}. "
        "Either seed the handler in SchedulerAssembly or override trigger_kind "
        "to 'on_demand'/'event'."
    )


# --------------------------------------------------------------------------- audit_owl_wiring


def test_audit_owl_wiring_self_heals_missing_internal_owl() -> None:
    """The owl-side sibling of the scheduler audit: an internal module can
    dispatch to a fixed owl name (e.g. staged_rca.RcaOwls' "verifier") without
    it ever being registered — triage.py silently reroutes to secretary on
    OwlNotFoundError, invisible for weeks. Missing required names must be
    auto-registered from their fallback factory, not merely flagged."""
    from stackowl.owls.manifest import OwlAgentManifest
    from stackowl.owls.registry import OwlRegistry

    registry = OwlRegistry.with_default_secretary()

    def _make_verifier() -> OwlAgentManifest:
        return OwlAgentManifest(
            name="verifier", role="rca-verifier", system_prompt="skeptical check",
            model_tier="powerful", tools=[],
        )

    report = audit_owl_wiring(registry, {"verifier": _make_verifier})
    assert report.healed == ["verifier"]
    assert registry.get("verifier").role == "rca-verifier"


def test_audit_owl_wiring_skips_already_registered() -> None:
    """An already-registered internal owl is left untouched — no re-heal,
    no duplicate registration attempt."""
    from stackowl.owls.manifest import OwlAgentManifest
    from stackowl.owls.registry import OwlRegistry

    registry = OwlRegistry.with_default_secretary()
    registry.register(
        OwlAgentManifest(
            name="verifier", role="already-here", system_prompt="x",
            model_tier="fast", tools=[],
        )
    )

    def _boom() -> OwlAgentManifest:
        raise AssertionError("must not be called — verifier is already registered")

    report = audit_owl_wiring(registry, {"verifier": _boom})
    assert report.healed == []
    assert registry.get("verifier").role == "already-here"
