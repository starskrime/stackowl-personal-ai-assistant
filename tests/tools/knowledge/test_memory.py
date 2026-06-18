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


class _FakePromoter:
    def __init__(self) -> None:
        self.promoted: list[str] = []

    async def force_promote(self, fact_id: str) -> None:
        self.promoted.append(fact_id)


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

async def test_add_then_search_recalls_it(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bridge = _FakeBridge()
    audit = _RecordingAudit()
    # Patch FactPromoter so _add doesn't need a real DbPool/SQLite.
    monkeypatch.setattr(
        "stackowl.tools.knowledge.memory.FactPromoter",
        lambda *_a, **_k: _FakePromoter(),
    )
    tool = MemoryTool()
    with _services(memory_bridge=bridge, db_pool=object(), audit_logger=audit):
        add = await tool.execute(action="add", content="the user prefers tabs over spaces")
        assert add.success
        assert "Remembered" in add.output  # visible mutating turn

        search = await tool.execute(action="search", query="tabs")
        assert search.success
        assert "tabs over spaces" in search.output
    # recall was delegated to the bridge (no Python glue).
    assert bridge.recall_calls and bridge.recall_calls[0][0] == "tabs"


async def test_add_tags_agent_self_source_type(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bridge = _FakeBridge()
    audit = _RecordingAudit()
    monkeypatch.setattr(
        "stackowl.tools.knowledge.memory.FactPromoter",
        lambda *_a, **_k: _FakePromoter(),
    )
    tool = MemoryTool()
    with _services(memory_bridge=bridge, db_pool=object(), audit_logger=audit):
        res = await tool.execute(action="add", content="prod db is in eu-west-1")
    assert res.success
    # The staged fact carries the agent_self provenance tag...
    assert bridge.staged_calls[0].source_type == "agent_self"
    # ...and the mutation was audited as memory.remember with that source_type.
    assert audit.rows and audit.rows[0]["event_type"] == "memory.remember"
    assert audit.rows[0]["details"]["source_type"] == "agent_self"  # type: ignore[index]


async def test_search_empty_returns_no_matches(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bridge = _FakeBridge()
    tool = MemoryTool()
    with _services(memory_bridge=bridge):
        res = await tool.execute(action="search", query="nothing here")
    assert res.success
    assert res.output == "(no matches)"


# --------------------------------------------------------------------- get/forget

async def test_get_existing_and_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bridge = _FakeBridge()
    bridge.facts.append(
        StagedFact(
            fact_id="abc123", content="hello world", source_type="agent_self",
            source_ref="tool:memory", confidence=1.0,
        )
    )
    tool = MemoryTool()
    with _services(memory_bridge=bridge):
        hit = await tool.execute(action="get", fact_id="abc")
        miss = await tool.execute(action="get", fact_id="zzz")
    assert hit.success and "hello world" in hit.output
    assert miss.success and "no fact matches" in miss.output  # structured no-op


async def test_forget_missing_id_structured_no_op() -> None:
    bridge = _FakeBridge()
    audit = _RecordingAudit()
    tool = MemoryTool()
    with _services(memory_bridge=bridge, audit_logger=audit):
        res = await tool.execute(action="forget", fact_id="does-not-exist")
    assert res.success
    assert "no fact matches" in res.output
    assert bridge.deleted == []  # no phantom delete
    assert audit.rows == []  # no audit row for a no-op


async def test_forget_ambiguous_prefix_refused() -> None:
    # M1: a prefix matching >1 fact must REFUSE, not delete an arbitrary one.
    bridge = _FakeBridge()
    audit = _RecordingAudit()
    for fid in ("aaa-111", "aaa-222"):
        bridge.facts.append(StagedFact(
            fact_id=fid, content="x", source_type="agent_self",
            source_ref="tool:memory", confidence=1.0,
        ))
    tool = MemoryTool()
    with _services(memory_bridge=bridge, audit_logger=audit):
        res = await tool.execute(action="forget", fact_id="aaa")
    assert res.success
    assert "ambiguous" in res.output
    assert bridge.deleted == []  # NOTHING deleted on an ambiguous id


async def test_forget_refuses_human_authored_fact() -> None:
    # M1: the agent's memory tool must not erase a human-authored ('manual') fact.
    bridge = _FakeBridge()
    audit = _RecordingAudit()
    bridge.facts.append(StagedFact(
        fact_id="user-real-1", content="user's real memory", source_type="manual",
        source_ref="cli:/memory", confidence=1.0,
    ))
    tool = MemoryTool()
    with _services(memory_bridge=bridge, audit_logger=audit):
        res = await tool.execute(action="forget", fact_id="user-real-1")
    assert res.success is False
    assert "Refusing to forget" in (res.error or "")
    assert bridge.deleted == []  # the human fact is NOT deleted


async def test_forget_existing_deletes_and_audits() -> None:
    bridge = _FakeBridge()
    audit = _RecordingAudit()
    bridge.facts.append(
        StagedFact(
            fact_id="del-me-456", content="ephemeral", source_type="agent_self",
            source_ref="tool:memory", confidence=1.0,
        )
    )
    tool = MemoryTool()
    with _services(memory_bridge=bridge, audit_logger=audit):
        res = await tool.execute(action="forget", fact_id="del-me")
    assert res.success and "Forgot" in res.output
    assert bridge.deleted == ["del-me-456"]
    assert audit.rows[0]["event_type"] == "memory.forget"


# --------------------------------------------------------------------- validation

async def test_invalid_action_did_you_mean() -> None:
    tool = MemoryTool()
    with _services(memory_bridge=_FakeBridge()):
        res = await tool.execute(action="serch")  # typo for search
    assert res.success is False
    assert res.error is not None
    assert "Unknown action" in res.error
    assert "Did you mean 'search'" in res.error  # first-char suggestion


async def test_missing_action_is_structured_error() -> None:
    tool = MemoryTool()
    with _services(memory_bridge=_FakeBridge()):
        res = await tool.execute()
    assert res.success is False
    assert res.error and "Unknown action" in res.error


async def test_add_without_content_is_structured_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "stackowl.tools.knowledge.memory.FactPromoter",
        lambda *_a, **_k: _FakePromoter(),
    )
    tool = MemoryTool()
    with _services(memory_bridge=_FakeBridge(), db_pool=object()):
        res = await tool.execute(action="add", content="   ")
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


async def test_add_without_db_pool_is_structured_unavailable() -> None:
    tool = MemoryTool()
    with _services(memory_bridge=_FakeBridge(), db_pool=None):
        res = await tool.execute(action="add", content="x")
    assert res.success is False
    assert res.error and "memory unavailable (add)" in res.error


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
