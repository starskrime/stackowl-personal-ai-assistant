"""Story 7.1 — JobHandler v2 surface, handlers, config, migration, B9."""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from stackowl.commands.registry import CommandRegistry
from stackowl.config.settings import SchedulerSettings, Settings
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import DomainError, SchedulerError
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.handlers.check_in import CheckInHandler
from stackowl.scheduler.handlers.goal_execution import GoalExecutionHandler
from stackowl.scheduler.handlers.knowledge_prune import KnowledgePruneHandler
from stackowl.scheduler.handlers.memory_consolidation import MemoryConsolidationHandler
from stackowl.scheduler.handlers.tool_pruning import ToolPruningHandler
from stackowl.scheduler.job import Job, JobResult

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _disable_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *_a, **_kw: None,
    )


def _job(handler: str = "check_in", **overrides: Any) -> Job:
    defaults: dict[str, Any] = dict(
        job_id=f"job-{uuid.uuid4().hex[:6]}",
        handler_name=handler,
        schedule="daily@09:00",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )
    defaults.update(overrides)
    return Job(**defaults)


class _RecordingDream(JobHandler):
    """Stand-in for DreamWorkerJobHandler used by MemoryConsolidation tests."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def handler_name(self) -> str:
        return "dream_worker"

    async def execute(self, job: Job) -> JobResult:
        self.calls.append(job.job_id)
        return JobResult(
            job_id=job.job_id,
            success=True,
            output="dream:ok",
            error=None,
            duration_ms=1.0,
            metadata={"facts_promoted": 7},
        )


class _FakePruner:
    """Stand-in for MemoryPruner that returns a controllable report."""

    def __init__(self, pruned: int = 4, kept: int = 12) -> None:
        self._pruned = pruned
        self._kept = kept

    async def prune(self) -> Any:
        from stackowl.memory.pruner import PruneReport

        return PruneReport(pruned_count=self._pruned, kept_count=self._kept)


# ---------------------------------------------------------------------------
# HandlerRegistry
# ---------------------------------------------------------------------------


class TestHandlerRegistry:
    def setup_method(self) -> None:
        HandlerRegistry.reset()

    def test_instance_is_singleton(self) -> None:
        a = HandlerRegistry.instance()
        b = HandlerRegistry.instance()
        assert a is b

    def test_list_returns_same_as_all(self) -> None:
        reg = HandlerRegistry.instance()
        reg.register(CheckInHandler())
        reg.register(ToolPruningHandler())
        assert reg.list() == reg.all()
        assert len(reg.list()) == 2

    def test_unregister_removes_handler(self) -> None:
        reg = HandlerRegistry.instance()
        reg.register(CheckInHandler())
        assert reg.get("check_in") is not None
        reg.unregister("check_in")
        assert reg.get("check_in") is None

    def test_unregister_unknown_is_noop(self) -> None:
        reg = HandlerRegistry.instance()
        reg.unregister("nonexistent")  # no exception

    def test_get_returns_none_for_unknown(self) -> None:
        reg = HandlerRegistry.instance()
        assert reg.get("does_not_exist") is None


# ---------------------------------------------------------------------------
# Handler names
# ---------------------------------------------------------------------------


class TestHandlerNames:
    def test_check_in_handler_name(self) -> None:
        assert CheckInHandler().handler_name == "check_in"

    def test_goal_execution_handler_name(self) -> None:
        assert GoalExecutionHandler().handler_name == "goal_execution"

    def test_memory_consolidation_handler_name(self) -> None:
        proxy = MemoryConsolidationHandler(dream_worker=_RecordingDream())
        assert proxy.handler_name == "memory_consolidation"

    def test_tool_pruning_handler_name(self) -> None:
        assert ToolPruningHandler().handler_name == "tool_pruning"

    def test_knowledge_prune_handler_name(self) -> None:
        h = KnowledgePruneHandler(pruner=_FakePruner())  # type: ignore[arg-type]
        assert h.handler_name == "knowledge_prune"


# ---------------------------------------------------------------------------
# Job / JobResult models
# ---------------------------------------------------------------------------


class TestJobModel:
    def test_job_has_v2_fields(self) -> None:
        job = _job(replay_missed=True, primary_channel="cli", params={"goal": "x"})
        assert job.failure_count == 0
        assert job.last_error is None
        assert job.enabled is True
        assert job.replay_missed is True
        assert job.primary_channel == "cli"
        assert job.params == {"goal": "x"}

    def test_job_result_has_metadata(self) -> None:
        r = JobResult(
            job_id="x",
            success=True,
            output="ok",
            error=None,
            duration_ms=1.0,
            metadata={"facts": 5},
        )
        assert r.metadata == {"facts": 5}


# ---------------------------------------------------------------------------
# SchedulerSettings / SchedulerError
# ---------------------------------------------------------------------------


class TestSchedulerConfig:
    def test_scheduler_settings_defaults(self) -> None:
        s = SchedulerSettings()
        assert s.max_concurrent_jobs == 3
        assert s.replay_window_hours == 24
        assert s.max_notifications_per_hour == 10

    def test_settings_includes_scheduler_field(self) -> None:
        cfg = Settings()
        assert isinstance(cfg.scheduler, SchedulerSettings)

    def test_scheduler_error_is_domain_error(self) -> None:
        assert issubclass(SchedulerError, DomainError)


# ---------------------------------------------------------------------------
# Handler execution (proxies + stubs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandlerExecution:
    async def test_check_in_unwired_skips_honestly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # C1/F102: the old permanent no-op ("check_in: noop") is gone. An
        # UNWIRED handler (no deliverer/db/settings) has nothing to assemble or
        # send, so it returns success with an HONEST skipped status — never a
        # fake delivery, never the stub sentinel output.
        _disable_test_mode_guard(monkeypatch)
        result = await CheckInHandler().execute(_job())
        assert result.success is True
        assert result.output is None
        assert result.metadata.get("delivery_status") == "skipped"

    async def test_goal_execution_returns_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_test_mode_guard(monkeypatch)
        result = await GoalExecutionHandler().execute(_job("goal_execution"))
        assert result.success is True

    async def test_memory_consolidation_proxies_to_dream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_test_mode_guard(monkeypatch)
        dream = _RecordingDream()
        proxy = MemoryConsolidationHandler(dream_worker=dream)
        job = _job("memory_consolidation")
        result = await proxy.execute(job)
        assert dream.calls == [job.job_id]
        assert result.success is True
        assert result.metadata == {"facts_promoted": 7}

    async def test_tool_pruning_returns_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_test_mode_guard(monkeypatch)
        result = await ToolPruningHandler().execute(_job("tool_pruning"))
        assert result.success is True

    async def test_knowledge_prune_proxies_to_pruner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _disable_test_mode_guard(monkeypatch)
        pruner = _FakePruner(pruned=4, kept=12)
        handler = KnowledgePruneHandler(pruner=pruner)  # type: ignore[arg-type]
        result = await handler.execute(_job("knowledge_prune"))
        assert result.success is True
        assert result.metadata == {"pruned_count": 4, "kept_count": 12}
        assert result.output is not None and "pruned=4" in result.output


# ---------------------------------------------------------------------------
# Migration 0018 + count
# ---------------------------------------------------------------------------


class TestMigration0018:
    def test_migration_file_exists(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "src/stackowl/db/migrations/0018_jobs_v2.sql"
        )
        assert path.exists()

    def test_migration_count_is_18(self, tmp_path: Path) -> None:
        # Name kept historical for log searchability. Asserts the runner applies
        # exactly the migration .sql files present on disk; the expected count is
        # derived dynamically from the actual .sql files (no more manual bumps on
        # every new migration).
        from stackowl.db.migrations.runner import MigrationRunner

        migrations_dir = (
            Path(__file__).resolve().parent.parent / "src/stackowl/db/migrations"
        )
        expected = len(sorted(migrations_dir.glob("*.sql")))
        results = MigrationRunner(db_path=tmp_path / "count.db").run()
        assert len(results) == expected


# ---------------------------------------------------------------------------
# B9 boundary script
# ---------------------------------------------------------------------------


class TestB9Boundary:
    def test_b9_passes_on_handler_files(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        b9 = repo_root / "scripts/boundaries/b9.py"
        proc = subprocess.run(
            [sys.executable, str(b9)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"B9 failed:\n{proc.stdout}\n{proc.stderr}"
        assert "B9 PASS" in proc.stdout


# ---------------------------------------------------------------------------
# Teardown — reset shared singletons
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singletons() -> Any:
    HandlerRegistry.reset()
    CommandRegistry.reset()
    yield
    HandlerRegistry.reset()
    CommandRegistry.reset()


# Touch TestModeGuard so the linter does not flag the import as unused.
_ = TestModeGuard
