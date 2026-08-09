"""Tests for the E4-S1 ``memory`` tool.

These exercise the tool's dispatch, provenance tagging, self-healing, and
manifest. They run against a FAITHFUL FAKE bridge (same recall/stage/delete/
list_staged surface as :class:`MemoryBridge`) rather than the real tri-store:
the real bridge pulls in LanceDB + Kuzu + a sentence-transformer embedder which
is flaky/heavy on the Jetson dev box (Kuzu file-lock + ST-model load — see
tests/memory/test_assembly.py which fails pre-existingly here). The fake lets us
assert the contract that matters — that ``remember_fact``/``forget_fact`` are
called with ``source_type=agent_self`` + audit, that recall is delegated to the
bridge with no Python glue, and that a down store degrades structurally — without
a live embedder.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from stackowl.memory.models import MemoryRecord, StagedFact
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.base import ToolManifest
from stackowl.tools.knowledge.memory import MemoryTool

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator


# --------------------------------------------------------------------- fakes


class _FakeBridge:
    """In-memory stand-in with the MemoryBridge knowledge-contract surface.

    ``stage`` appends to a committed list (we skip the staged/promote dance —
    the FakePromoter below force-promotes), so a subsequent ``recall`` /
    ``list_staged`` can see the fact, giving the tool a realistic round-trip.
    """

    def __init__(self) -> None:
        self.facts: list[StagedFact] = []
        self.staged_calls: list[StagedFact] = []
        self.deleted: list[str] = []
        self.recall_calls: list[tuple[str, int]] = []

    async def stage(self, fact: StagedFact) -> None:
        self.staged_calls.append(fact)
        self.facts.append(fact)

    async def delete(self, fact_id: str) -> None:
        self.deleted.append(fact_id)
        self.facts = [f for f in self.facts if f.fact_id != fact_id]

    async def recall(self, query: str, limit: int = 10) -> list[MemoryRecord]:
        self.recall_calls.append((query, limit))
        out: list[MemoryRecord] = []
        for f in self.facts:
            if query.lower() in f.content.lower():
                out.append(
                    MemoryRecord(
                        fact_id=f.fact_id,
                        content=f.content,
                        embedding=[0.0],
                        embedding_model="fake",
                        committed_at=datetime.now(UTC),
                        source_type=f.source_type,
                        source_ref=f.source_ref,
                    )
                )
        return out[:limit]

    async def list_staged(
        self, status: Literal["staged", "committed", "rejected"] = "staged"
    ) -> list[StagedFact]:
        # All fakes live in one bucket; only surface them under 'staged'/'committed'.
        if status == "rejected":
            return []
        return list(self.facts)


class _RecordingAudit:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def append(self, *, event_type: str, actor: str, target: str | None,
               details: dict[str, object]) -> None:
        self.rows.append(
            {"event_type": event_type, "actor": actor, "target": target, "details": details}
        )


@contextmanager
def _services(**kw: object) -> Iterator[None]:
    token = set_services(StepServices(**kw))  # type: ignore[arg-type]
    try:
        yield
    finally:
        reset_services(token)


# --------------------------------------------------------------------- add/search

async def test_add_then_search_recalls_it(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """MIGRATED (D08.1 slice 2): `add` writes to the CURATED FILE, not the fact
    store. The guarantee this test always made — write it, then find it — still
    holds, and `search` spanning both surfaces is what keeps it true."""
    from stackowl.memory.curated import CuratedMemory

    monkeypatch.setattr(
        MemoryTool, "_curated", lambda self: CuratedMemory(root=tmp_path / "memory"),
    )
    bridge = _FakeBridge()
    tool = MemoryTool()
    with _services(memory_bridge=bridge, db_pool=object(), audit_logger=_RecordingAudit()):
        add = await tool.execute(
            action="add", content="the user prefers tabs over spaces",
            durability="permanent",
        )
        assert add.success, add.error

        search = await tool.execute(action="search", query="tabs")

    assert "tabs over spaces" in search.output
    assert bridge.staged_calls == [], "nothing should reach the fact store any more"


async def test_add_no_longer_writes_to_the_fact_store(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """REPLACES test_add_tags_agent_self_source_type (D08.1 slice 2).

    That test guarded provenance tagging on a write path that no longer exists:
    measured 2026-08-08, the fact store had reached 88,631 entries, 37.1% of them
    trace telemetry, with a three-week-stale date as its top-ranked item. The
    write path is retired; what needs guarding now is that it STAYS retired, or
    the store starts refilling the moment someone re-wires `add`.
    """
    from stackowl.memory.curated import CuratedMemory

    monkeypatch.setattr(
        MemoryTool, "_curated", lambda self: CuratedMemory(root=tmp_path / "memory"),
    )
    bridge = _FakeBridge()
    audit = _RecordingAudit()
    tool = MemoryTool()
    with _services(memory_bridge=bridge, db_pool=object(), audit_logger=audit):
        res = await tool.execute(
            action="add", content="prod db is in eu-west-1", durability="permanent",
        )

    assert res.success, res.error
    assert bridge.staged_calls == []
    assert (tmp_path / "memory" / "USER.md").exists()


async def test_add_without_content_is_structured_error() -> None:
    tool = MemoryTool()
    with _services(memory_bridge=_FakeBridge(), db_pool=object()):
        res = await tool.execute(action="add", content="   ", durability="permanent")
    assert res.success is False
    assert res.error and "requires 'content'" in res.error


# --------------------------------------------------------------------- self-healing

async def test_store_unavailable_when_bridge_none() -> None:
    tool = MemoryTool()
    with _services(memory_bridge=None):
        res = await tool.execute(action="search", query="x")
    assert res.success is False
    assert res.error and "memory unavailable" in res.error  # structured, no raise


async def test_store_failure_degrades_without_raising() -> None:
    class _ExplodingBridge(_FakeBridge):
        async def recall(self, query: str, limit: int = 10) -> list[MemoryRecord]:
            raise RuntimeError("lancedb down")

    tool = MemoryTool()
    with _services(memory_bridge=_ExplodingBridge()):
        res = await tool.execute(action="search", query="x")
    # No raise — degraded to a structured failed result naming the action.
    assert res.success is False
    assert res.error and "memory unavailable (search)" in res.error


async def test_add_does_not_need_a_database(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """REPLACES test_add_without_db_pool_is_structured_unavailable.

    Curated memory is two files. A write no longer touches SQLite, LanceDB or
    Kuzu — which is most of the point: remembering something about the user
    should not depend on three stores being healthy.
    """
    from stackowl.memory.curated import CuratedMemory

    monkeypatch.setattr(
        MemoryTool, "_curated", lambda self: CuratedMemory(root=tmp_path / "memory"),
    )
    tool = MemoryTool()
    with _services(memory_bridge=_FakeBridge(), db_pool=None):
        res = await tool.execute(action="add", content="x", durability="permanent")

    assert res.success is True, res.error


# --------------------------------------------------------------------- manifest/registry

def test_manifest_severity_and_group() -> None:
    m: ToolManifest = MemoryTool().manifest
    assert m.action_severity == "write"
    assert m.toolset_group == "knowledge"
    assert m.name == "memory"
    # Description must state lane + anti-lane.
    assert "session_search" in m.description
    assert "skill_view" in m.description


def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry.with_defaults()
    assert reg.get("memory") is not None
    assert isinstance(reg.get("memory"), MemoryTool)
