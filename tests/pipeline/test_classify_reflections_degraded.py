"""F-49 — learned-reflection recall must not silently revert to memoryless.

The one live read of learned reflections before acting
(`classify._gather_recent_reflections`) used to catch every exception and
return "" — so the turn proceeded memoryless with no retry and no signal,
silently reverting to memoryless EXACTLY when memory was broken.

These tests pin the fixed behaviour:
  * a legitimate empty table → "" (unchanged),
  * a recall FAILURE → retried once, then annotated DEGRADED (never silent),
  * the function never raises (the turn stays alive).
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.reflection_store import ReflectionStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.steps import classify
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

pytestmark = pytest.mark.asyncio


class _FailingDb:
    """A db_pool stand-in whose every read raises — simulates broken recall."""

    def __init__(self) -> None:
        self.fetch_all_calls = 0

    async def fetch_all(self, sql: str, params: object = ()) -> list[dict[str, object]]:  # noqa: ARG002
        self.fetch_all_calls += 1
        raise RuntimeError("recall boom")


async def test_recall_failure_retries_once_then_annotates_degraded() -> None:
    fake = _FailingDb()
    token = set_services(StepServices(db_pool=fake))  # type: ignore[arg-type]
    try:
        block = await classify._gather_recent_reflections("secretary", limit=3)
    finally:
        reset_services(token)

    # Retry-once semantics: exactly two attempts before giving up.
    assert fake.fetch_all_calls == 2
    # The turn is told memory is degraded rather than silently empty.
    assert "DEGRADED" in block
    assert block != ""


async def test_legitimate_empty_returns_empty_no_degraded(tmp_db: DbPool) -> None:
    token = set_services(StepServices(db_pool=tmp_db))
    try:
        block = await classify._gather_recent_reflections("secretary", limit=3)
    finally:
        reset_services(token)

    # No reflections is a legitimate empty — NOT a degradation.
    assert block == ""
    assert "DEGRADED" not in block


async def test_success_path_surfaces_reflections(tmp_db: DbPool) -> None:
    store = ReflectionStore(tmp_db, owner_id=DEFAULT_PRINCIPAL_ID)
    await store.write(
        trace_id="t-1", owl_name="secretary",
        summary="batched the writes", suggested_strategy="prefer bulk insert",
        failure_class=None, quality_score=0.9,
        embedding=None, embedding_model=None,
    )

    token = set_services(StepServices(db_pool=tmp_db))
    try:
        block = await classify._gather_recent_reflections("secretary", limit=3)
    finally:
        reset_services(token)

    assert "## Recent Reflections" in block
    assert "batched the writes" in block
    assert "DEGRADED" not in block
