"""Story 6.5 (part A) — KuzuAdapter and EntityExtractor unit tests."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from stackowl.memory.entity_extractor import EntityExtractor
from stackowl.memory.kuzu_adapter import KuzuAdapter
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.registry import ModelRoute, ProviderRegistry

from tests._story_6_5_helpers import (  # noqa: F401 — re-exports
    RaisingConn,
    StubProvider,
    StubRegistry,
    adapter,
    no_test_mode_guard,
)


# ---------------------------------------------------------------------------
# KuzuAdapter
# ---------------------------------------------------------------------------


async def test_kuzu_adapter_schema_created(adapter: KuzuAdapter) -> None:
    """T1 — _create_schema runs without error on fresh dir."""
    report = await adapter.health()
    assert report.status == "ok"


async def test_kuzu_upsert_entity_readable(adapter: KuzuAdapter) -> None:
    """T2 — entity readable after upsert."""
    await adapter.upsert_entity("ent_a", "Alice", "PERSON", "f1")
    from stackowl.memory.kuzu_helpers import _rows_from_result

    result = adapter._conn.execute(
        "MATCH (e:Entity {id: $id}) RETURN e.name AS name", {"id": "ent_a"}
    )
    rows = _rows_from_result(result)
    assert rows[0]["name"] == "Alice"


async def test_kuzu_upsert_fact_node_readable(adapter: KuzuAdapter) -> None:
    """T3 — fact node readable after upsert."""
    await adapter.upsert_fact_node("f1", "Some fact content", 0.9)
    from stackowl.memory.kuzu_helpers import _rows_from_result

    result = adapter._conn.execute(
        "MATCH (f:Fact {id: $id}) RETURN f.content AS content", {"id": "f1"}
    )
    rows = _rows_from_result(result)
    assert rows[0]["content"] == "Some fact content"


async def test_kuzu_link_fact_to_entity(adapter: KuzuAdapter) -> None:
    """T4 — MENTIONS edge exists after link."""
    await adapter.upsert_fact_node("f1", "x", 0.5)
    await adapter.upsert_entity("ent_a", "Alice", "PERSON", "f1")
    await adapter.link_fact_to_entity("f1", "ent_a", "mentions")
    from stackowl.memory.kuzu_helpers import _rows_from_result

    result = adapter._conn.execute(
        "MATCH (f:Fact {id: $fid})-[:MENTIONS]->(e:Entity) RETURN e.id AS eid",
        {"fid": "f1"},
    )
    rows = _rows_from_result(result)
    assert any(r["eid"] == "ent_a" for r in rows)


async def test_kuzu_traverse_returns_entities(adapter: KuzuAdapter) -> None:
    """T5 — traverse returns entities within max_hops."""
    await adapter.upsert_entity("a", "Alpha", "TOPIC", "f1")
    await adapter.upsert_entity("b", "Beta", "TOPIC", "f1")
    await adapter.upsert_entity("c", "Gamma", "TOPIC", "f1")
    await adapter.link_entities("a", "b", "rel", strength=1.0)
    await adapter.link_entities("b", "c", "rel", strength=1.0)
    rows = await adapter.traverse("a", max_hops=2)
    names = {r["name"] for r in rows}
    assert "Beta" in names and "Gamma" in names


async def test_kuzu_traverse_unknown_returns_empty(adapter: KuzuAdapter) -> None:
    """T6 — unknown entity returns []."""
    rows = await adapter.traverse("does_not_exist", max_hops=2)
    assert rows == []


async def test_kuzu_health_ok(adapter: KuzuAdapter) -> None:
    """T7 — health returns ok on fresh adapter."""
    report = await adapter.health()
    assert report.status == "ok"
    assert report.name == "memory.kuzu"
    assert "data_dir" in report.details


async def test_kuzu_health_down_on_failure(adapter: KuzuAdapter) -> None:
    """T8 — health returns down when probe raises."""
    adapter._conn = RaisingConn()  # type: ignore[assignment]
    report = await adapter.health()
    assert report.status == "down"
    assert "error" in report.details


async def test_kuzu_link_entities_creates_edge(adapter: KuzuAdapter) -> None:
    """T16 — RELATED_TO edge exists after link_entities."""
    await adapter.upsert_entity("e1", "One", "TOPIC", "f1")
    await adapter.upsert_entity("e2", "Two", "TOPIC", "f1")
    await adapter.link_entities("e1", "e2", "related", strength=0.5)
    from stackowl.memory.kuzu_helpers import _rows_from_result

    result = adapter._conn.execute(
        "MATCH (a:Entity {id: $a})-[r:RELATED_TO]->(b:Entity {id: $b}) "
        "RETURN r.relation AS rel, r.strength AS st",
        {"a": "e1", "b": "e2"},
    )
    rows = _rows_from_result(result)
    assert rows[0]["rel"] == "related"
    assert abs(float(rows[0]["st"]) - 0.5) < 1e-5


async def test_kuzu_traverse_respects_max_hops(adapter: KuzuAdapter) -> None:
    """T17 — max_hops=1 must NOT reach 2-hop neighbor."""
    await adapter.upsert_entity("h1", "H1", "TOPIC", "f")
    await adapter.upsert_entity("h2", "H2", "TOPIC", "f")
    await adapter.upsert_entity("h3", "H3", "TOPIC", "f")
    await adapter.link_entities("h1", "h2", "rel", 1.0)
    await adapter.link_entities("h2", "h3", "rel", 1.0)
    one_hop = await adapter.traverse("h1", max_hops=1)
    two_hop = await adapter.traverse("h1", max_hops=2)
    names_one = {r["name"] for r in one_hop}
    names_two = {r["name"] for r in two_hop}
    assert "H2" in names_one and "H3" not in names_one
    assert "H2" in names_two and "H3" in names_two


def test_kuzu_test_mode_guard_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T19 — TestModeGuard.assert_not_test_mode is called before live writes."""
    calls: list[str] = []

    def _spy(op: str) -> None:
        calls.append(op)

    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode", _spy
    )
    adp = KuzuAdapter(data_dir=tmp_path / "guarded")
    import asyncio

    asyncio.run(adp.upsert_entity("g1", "G", "TOPIC", "f1"))
    assert "kuzu.upsert_entity" in calls


# ---------------------------------------------------------------------------
# EntityExtractor
# ---------------------------------------------------------------------------


async def test_entity_extractor_happy_path() -> None:
    """T9 — extract returns parsed entities on valid LLM response."""
    response = (
        '[{"name": "Alice", "entity_type": "PERSON", "mentions": ["Alice met"]}, '
        '{"name": "Acme", "entity_type": "ORG", "mentions": ["at Acme"]}]'
    )
    extractor = EntityExtractor(
        provider_registry=StubRegistry(StubProvider(response)),  # type: ignore[arg-type]
    )
    entities = await extractor.extract("Alice met at Acme", "f1")
    assert len(entities) == 2
    assert entities[0].name == "Alice"
    assert entities[0].entity_type == "PERSON"


async def test_entity_extractor_parse_failure_returns_empty() -> None:
    """T10 — malformed JSON degrades to []."""
    extractor = EntityExtractor(
        provider_registry=StubRegistry(  # type: ignore[arg-type]
            StubProvider("not-json {oops")
        ),
    )
    entities = await extractor.extract("anything", "f1")
    assert entities == []


async def test_entity_extractor_sensitive_short_circuits() -> None:
    """T11 — sensitive content is skipped without an LLM call."""
    provider = StubProvider("[]")
    extractor = EntityExtractor(
        provider_registry=StubRegistry(provider),  # type: ignore[arg-type]
        sensitive_categories=[r"\bpassword\b"],
    )
    entities = await extractor.extract("my password is hunter2", "f1")
    assert entities == []
    assert provider.calls == []  # provider was never asked


async def test_entity_extractor_handles_malformed_json() -> None:
    """T20 — entirely broken JSON returns [] (not raise)."""
    extractor = EntityExtractor(
        provider_registry=StubRegistry(  # type: ignore[arg-type]
            StubProvider("```json\nthis is not json at all```")
        ),
    )
    entities = await extractor.extract("anything", "f1")
    assert entities == []


# ---------------------------------------------------------------------------
# Task 16 — EntityExtractor._resolve_provider() threads the resolved (provider,
# model) tuple through to provider.complete(), instead of hardcoding model="".
# ---------------------------------------------------------------------------


class _ModelCapturingEntityProvider(ModelProvider):
    """Records the ``model`` kwarg its ``complete()`` was called with — proves
    ``EntityExtractor.extract()`` forwards the RESOLVED model rather than
    hardcoding ``model=""``. Returns a fixed, valid entity list."""

    def __init__(self) -> None:
        self.seen_models: list[str] = []

    @property
    def name(self) -> str:
        return "model-capturing-entity"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.seen_models.append(model)
        return CompletionResult(
            content='[{"name": "Alice", "entity_type": "PERSON", "mentions": ["Alice"]}]',
            input_tokens=5,
            output_tokens=5,
            model="model-capturing-entity",
            provider_name="model-capturing-entity",
            duration_ms=0.5,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        raise NotImplementedError
        yield ""  # pragma: no cover — unreachable, satisfies async generator typing


async def test_entity_extractor_threads_resolved_second_tier_model() -> None:
    """A provider registered with TWO models on different tiers must resolve
    the model belonging to the tier EntityExtractor actually requests — proving
    ``_resolve_provider()`` threads ``get_with_cascade_and_model()``'s (provider,
    model) tuple through to ``provider.complete()`` rather than dropping the
    model half of the tuple (the old ``get_with_cascade()`` behaviour) or always
    reaching for the first-registered route.

    Genuinely discriminating: if ``_resolve_provider()`` still called
    ``get_with_cascade`` (provider only) and ``extract()`` kept hardcoding
    ``model=""``, ``seen_models`` would be ``[""]`` instead of the SECOND
    model's sentinel string.
    """
    capturing_provider = _ModelCapturingEntityProvider()
    registry = ProviderRegistry()
    registry.register_mock(
        "dual-model",
        capturing_provider,
        models=(
            ModelRoute(model="entity-fast-model", tiers=("fast",)),
            ModelRoute(model="entity-standard-model", tiers=("standard",)),
        ),
    )
    # preferred_tier="standard" is served ONLY by the second registered route —
    # a match here can only come from resolving the SECOND model.
    extractor = EntityExtractor(provider_registry=registry, preferred_tier="standard")
    entities = await extractor.extract("Alice works here", "f-model-thread")
    assert entities, "extractor must return at least one entity"
    assert capturing_provider.seen_models == ["entity-standard-model"], (
        f"expected the SECOND (standard-tier) model to reach provider.complete, "
        f"got: {capturing_provider.seen_models!r}"
    )
