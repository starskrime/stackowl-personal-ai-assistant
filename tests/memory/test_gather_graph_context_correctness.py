"""Task 9 (Part 1) — real-traversal correctness test for `_gather_graph_context`.

`KuzuAdapter.traverse()` has exactly one production caller: `_gather_graph_context`
in `stackowl.pipeline.steps.classify`. Before this test, the ONLY coverage of
`traverse()` was `test_kuzu_thread_confinement.py`, which asserts thread-safety
under contention but never checks that the traversal actually returns the right
entities. A broken Cypher query, a wrong column mapping, or a wrong entity-id
derivation would all pass that test silently.

This test seeds a REAL on-disk Kuzu graph (no mock of `sync_traverse`/`Connection`)
using the exact deterministic entity-id derivation `classify._candidate_entity_ids`
uses in production, links a second entity, then drives `_gather_graph_context`
end-to-end (services wiring -> candidate-id derivation -> real traverse -> real
formatting) and asserts the linked entity's name/type surfaces in the result.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.services import StepServices, reset_services, set_services

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def _kuzu_available() -> None:
    pytest.importorskip("kuzu")


async def test_gather_graph_context_surfaces_real_linked_entity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _kuzu_available: None,
) -> None:
    monkeypatch.setattr(
        TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None)
    )

    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.pipeline.steps.classify import (
        _candidate_entity_ids,
        _gather_graph_context,
    )

    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    try:
        # Same digest formula classify.py uses on the live path — the FIRST id
        # for a token is always the PERSON-typed candidate (fixed type ordering).
        entity_id = _candidate_entity_ids("Alice")[0]
        await adapter.upsert_entity(entity_id, "Alice", "PERSON", "fact-1")
        await adapter.upsert_entity("bob-id", "Bob Marley", "PERSON", "fact-1")
        await adapter.link_entities(entity_id, "bob-id", "colleague", 1.0)

        services = StepServices(kuzu_adapter=adapter)
        token = set_services(services)
        try:
            context = await _gather_graph_context("Alice")
        finally:
            reset_services(token)
    finally:
        await adapter.aclose()

    assert "Related entities:" in context
    assert "Bob Marley (PERSON)" in context


async def test_gather_graph_context_empty_when_nothing_linked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _kuzu_available: None,
) -> None:
    """Sanity check the test's own fidelity: an unlinked/unknown query yields
    no context — proving the positive assertion above is exercising the real
    traversal (finds something when seeded, nothing when not), not vacuously
    true regardless of graph contents."""
    monkeypatch.setattr(
        TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None)
    )

    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.pipeline.steps.classify import _gather_graph_context

    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    try:
        services = StepServices(kuzu_adapter=adapter)
        token = set_services(services)
        try:
            context = await _gather_graph_context("NobodyKnownAtAll")
        finally:
            reset_services(token)
    finally:
        await adapter.aclose()

    assert context == ""


async def test_gather_graph_context_logs_then_degrades_on_traverse_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _kuzu_available: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verifies the existing log-then-degrade contract on a real traverse failure:
    a WARNING is logged with `exc_info` (never a silent except), and the turn
    still degrades gracefully (empty context, no raise) rather than crashing."""
    import logging

    monkeypatch.setattr(
        TestModeGuard, "assert_not_test_mode", staticmethod(lambda _op: None)
    )

    from stackowl.memory.kuzu_adapter import KuzuAdapter
    from stackowl.pipeline.steps.classify import _gather_graph_context

    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")

    async def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("simulated traverse failure")

    monkeypatch.setattr(adapter, "traverse", _boom)

    try:
        services = StepServices(kuzu_adapter=adapter)
        token = set_services(services)
        try:
            with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
                context = await _gather_graph_context("Alice")
        finally:
            reset_services(token)
    finally:
        await adapter.aclose()

    # Degrades gracefully — no crash, no phantom context.
    assert context == ""
    # But the failure is LOGGED, not silently swallowed.
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("kuzu traverse failed" in r.getMessage() for r in warnings), (
        "traverse failure must be logged, not silently swallowed"
    )
    assert any(r.exc_info is not None for r in warnings), (
        "the logged failure must carry exc_info for debuggability"
    )
