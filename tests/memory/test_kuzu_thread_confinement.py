"""F067 (P1) merge-gate — Kuzu Connection access is confined to ONE thread.

A single shared ``kuzu.Connection`` was driven through ``run_in_executor(None, ...)``
— the DEFAULT multi-worker pool — by every op. A live ``classify`` traverse and a
dream-worker ``kuzu_sync`` upsert could drive the same non-thread-safe Connection
from two threads concurrently; ``traverse`` swallows to ``[]`` so the failure
showed up as a silently-empty graph context under contention.

Fix: a dedicated ``ThreadPoolExecutor(max_workers=1)`` — race-free by
construction. This drives the REAL concurrent path (gather of traverse + upsert)
and asserts every Kuzu op landed on the SAME thread id with no exception. A green
unit test of either op alone would miss the race entirely.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def _kuzu_available() -> None:
    pytest.importorskip("kuzu")


async def test_all_kuzu_ops_on_single_thread_under_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _kuzu_available: None
) -> None:
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None))

    import stackowl.memory.kuzu_adapter as ka

    seen_threads: set[int] = set()

    # Wrap the real sync bodies to record which thread each ran on. The bodies
    # still execute against the real Connection — we assert confinement, not a mock.
    real_traverse = ka.sync_traverse
    real_upsert_entity = ka.sync_upsert_entity
    real_upsert_fact = ka.sync_upsert_fact
    real_link_entities = ka.sync_link_entities

    def _wrap(fn):  # type: ignore[no-untyped-def]
        def inner(*args, **kwargs):  # type: ignore[no-untyped-def]
            seen_threads.add(threading.get_ident())
            return fn(*args, **kwargs)

        return inner

    monkeypatch.setattr(ka, "sync_traverse", _wrap(real_traverse))
    monkeypatch.setattr(ka, "sync_upsert_entity", _wrap(real_upsert_entity))
    monkeypatch.setattr(ka, "sync_upsert_fact", _wrap(real_upsert_fact))
    monkeypatch.setattr(ka, "sync_link_entities", _wrap(real_link_entities))

    adapter = ka.KuzuAdapter(data_dir=tmp_path / "kuzu")
    try:
        # Seed two linked entities so traverse has something to walk.
        await adapter.upsert_entity("e1", "Alice", "person", "f1")
        await adapter.upsert_entity("e2", "Baku", "place", "f1")
        await adapter.link_entities("e1", "e2", "lives_in", 1.0)

        # Fire many concurrent traverse + upsert ops at once — the contention path.
        tasks = []
        for i in range(20):
            tasks.append(adapter.traverse("e1", max_hops=2))
            tasks.append(adapter.upsert_entity(f"x{i}", f"name{i}", "thing", "f1"))
            tasks.append(adapter.upsert_fact_node(f"fn{i}", f"content {i}", 1.0))
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await adapter.aclose()

    # No op raised — concurrent access did not corrupt the Connection.
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"concurrent Kuzu ops raised: {errors}"

    # Every recorded op ran on exactly ONE thread (the dedicated kuzu worker).
    assert len(seen_threads) == 1, (
        f"Kuzu ops must be confined to a single thread; saw {len(seen_threads)}: {seen_threads}"
    )
    # That thread is NOT the event-loop thread (work was offloaded, loop unblocked).
    assert seen_threads != {threading.get_ident()}


async def test_aclose_shuts_down_executor_no_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _kuzu_available: None
) -> None:
    monkeypatch.setattr(TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None))
    from stackowl.memory.kuzu_adapter import KuzuAdapter

    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    await adapter.upsert_entity("e1", "Alice", "person", "f1")
    await adapter.aclose()
    # The executor is shut down; submitting after shutdown raises.
    assert adapter._executor._shutdown is True  # type: ignore[attr-defined]
