"""Shared fixtures and stubs for Story 6.5 tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.kuzu_adapter import KuzuAdapter
from stackowl.providers.base import CompletionResult, Message
from stackowl.scheduler.job import Job


@pytest.fixture(autouse=True)
def no_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable :class:`TestModeGuard` for all Story 6.5 tests."""
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncGenerator[DbPool, None]:
    """Per-test fresh DbPool with all migrations applied."""
    db_path = tmp_path / "story65.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture()
def adapter(tmp_path: Path) -> KuzuAdapter:
    """Per-test KuzuAdapter rooted at a temp directory."""
    return KuzuAdapter(data_dir=tmp_path / "kuzu")


class StubProvider:
    """Minimal :class:`ModelProvider` stub returning a canned response."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return "stub"

    @property
    def protocol(self) -> str:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.calls.append(list(messages))
        return CompletionResult(
            content=self._response,
            input_tokens=10,
            output_tokens=20,
            model="stub",
            provider_name="stub",
            duration_ms=1.0,
        )

    def stream(self, messages: list[Message], model: str, **kwargs: object) -> Any:
        raise NotImplementedError


class StubRegistry:
    """Provider registry stub exposing ``get_with_cascade`` /
    ``get_with_cascade_and_model``."""

    def __init__(self, provider: StubProvider | None) -> None:
        self._provider = provider

    def get_with_cascade(self, preferred_tier: str) -> Any:
        if self._provider is None:
            raise RuntimeError("no provider")
        return self._provider

    def get_with_cascade_and_model(self, preferred_tier: str) -> Any:
        """Task 16 — EntityExtractor._resolve_provider() now calls this instead
        of get_with_cascade(). Byte-identical model="" default (matches every
        existing Story 6.5 test's expectations, none of which pin a model)."""
        return self.get_with_cascade(preferred_tier), ""


class RaisingConn:
    """Stand-in for kuzu.Connection that fails every execute."""

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")


async def insert_committed(
    pool: DbPool, fact_id: str, content: str, committed_at: str | None = None
) -> None:
    """Insert a single committed_fact for sync-handler tests."""
    iso = committed_at or datetime.now(UTC).isoformat()
    await pool.execute(
        """INSERT INTO committed_facts
               (fact_id, content, embedding, embedding_model, committed_at,
                source_type, source_ref, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (fact_id, content, b"\x00" * 16, "stub", iso, "conversation", "sess", "[]"),
    )


def make_job(job_id: str = "j-1") -> Job:
    """Minimal :class:`Job` for handler tests."""
    return Job(
        job_id=job_id,
        handler_name="kuzu_sync",
        schedule="manual",
        idempotency_key=f"kuzu_sync:{job_id}",
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )
