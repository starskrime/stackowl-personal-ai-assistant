"""LAT.1 — SkillsAssembly.build()'s three back-fill passes must read the skill
index once per pass (via SkillIndexStore.index_by_source_name), not once per
skill via SkillIndexStore.get. Mirrors test_lessons_publish_cache.py /
test_summarize_backfill.py's fixture style.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from stackowl.owls.registry import OwlRegistry
from stackowl.skills.assembly import SkillsAssembly
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.registry import ToolRegistry


@dataclass
class _StubEmbeddingProvider:
    dim: int = 8
    model_name: str = "stub-embed-v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                digest = hashlib.sha1(tok.encode("utf-8")).digest()
                vec[digest[0] % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / n for x in vec])
        return out


@dataclass
class _StubEmbeddingRegistry:
    provider: _StubEmbeddingProvider = field(default_factory=_StubEmbeddingProvider)

    def get(self) -> _StubEmbeddingProvider:
        return self.provider


@dataclass
class _StubCompleteResult:
    content: str = "Do X. Then Y."


@dataclass
class _StubProvider:
    calls: int = 0

    async def complete(self, messages, **kw):
        self.calls += 1
        return _StubCompleteResult()


@dataclass
class _StubProviderRegistry:
    provider: _StubProvider = field(default_factory=_StubProvider)

    def get_with_cascade(self, tier):
        return self.provider, ""


@dataclass
class _StubLessonsIndex:
    calls: int = 0
    published: list = field(default_factory=list)

    async def publish_many(self, drafts):
        self.calls += 1
        self.published.append(list(drafts))
        return len(drafts)


def _write_skill(root: Path, name: str, *, source: str = "user", body: str = "body text") -> None:
    d = root / source / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\n{body}\n", encoding="utf-8",
    )


async def _build(tmp_db, root, **kw):
    return await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=root, builtin_seed_dir=root / "none", **kw,
    )


@pytest.mark.asyncio
async def test_each_pass_issues_exactly_one_index_read_regardless_of_skill_count(
    monkeypatch, tmp_db, tmp_path,
):
    """50 skills, all 3 passes active -> 3 index_by_source_name calls total, 0 store.get calls."""
    for i in range(50):
        _write_skill(tmp_path, f"skill-{i}")

    index_calls = 0
    orig_index = SkillIndexStore.index_by_source_name

    async def counting_index(self):
        nonlocal index_calls
        index_calls += 1
        return await orig_index(self)

    get_calls = 0
    orig_get = SkillIndexStore.get

    async def counting_get(self, source, name):
        nonlocal get_calls
        get_calls += 1
        return await orig_get(self, source, name)

    monkeypatch.setattr(SkillIndexStore, "index_by_source_name", counting_index)
    monkeypatch.setattr(SkillIndexStore, "get", counting_get)

    await _build(
        tmp_db, tmp_path,
        embedding_registry=_StubEmbeddingRegistry(),
        provider_registry=_StubProviderRegistry(),
        lessons_index=_StubLessonsIndex(),
    )

    # One snapshot per active pass (_embed_missing, _summarize_missing,
    # _publish_to_lessons) — not one per skill (would be 150 for 50 skills x 3 passes).
    assert index_calls == 3
    # The passes must do dict lookups against the snapshot, not per-skill store.get.
    assert get_calls == 0


@pytest.mark.asyncio
async def test_publish_to_lessons_sees_embedding_written_earlier_in_same_build(
    monkeypatch, tmp_db, tmp_path,
):
    """Per-pass (not shared) snapshots: _publish_to_lessons's snapshot, taken
    after _embed_missing has already run in the same build() call, must show
    the embedding _embed_missing just wrote. A shared snapshot taken once up
    front would show embedding=None here (the regression this guards against).
    """
    _write_skill(tmp_path, "alpha")

    snapshots: list[dict] = []
    orig_index = SkillIndexStore.index_by_source_name

    async def recording_index(self):
        result = await orig_index(self)
        snapshots.append(dict(result))
        return result

    monkeypatch.setattr(SkillIndexStore, "index_by_source_name", recording_index)

    await _build(
        tmp_db, tmp_path,
        embedding_registry=_StubEmbeddingRegistry(),
        lessons_index=_StubLessonsIndex(),
    )

    # Order in SkillsAssembly.build(): embed pass snapshot first, publish pass second.
    assert len(snapshots) == 2
    embed_pass_snapshot, publish_pass_snapshot = snapshots
    key = ("user", "alpha")
    assert embed_pass_snapshot[key].embedding is None  # taken before the write
    assert publish_pass_snapshot[key].embedding is not None  # taken after the write
    assert publish_pass_snapshot[key].embedding_model == "stub-embed-v1"


@pytest.mark.asyncio
async def test_full_build_mixed_skill_set_matches_per_skill_read_decisions(tmp_db, tmp_path):
    """Mixed set: one skill already embedded+summarized+published (unchanged
    body -> all 3 passes must skip), one brand-new skill (all 3 must write).
    Verifies the batched-read implementation reaches the same skip/write
    decisions as the per-skill-read implementation it replaces.
    """
    _write_skill(tmp_path, "stale", body="unchanged forever")
    embed_registry = _StubEmbeddingRegistry()
    provider_registry = _StubProviderRegistry()
    lessons_index = _StubLessonsIndex()

    # Boot 1: "stale" gets embedded, summarized, published.
    await _build(
        tmp_db, tmp_path,
        embedding_registry=embed_registry,
        provider_registry=provider_registry,
        lessons_index=lessons_index,
    )
    assert provider_registry.provider.calls == 1
    assert lessons_index.calls == 1

    # Add a brand-new skill; "stale" is untouched on disk.
    _write_skill(tmp_path, "fresh", body="brand new content")

    embed_calls: list[int] = []
    orig_embed = embed_registry.provider.embed

    async def counting_embed(texts):
        embed_calls.append(len(texts))
        return await orig_embed(texts)

    embed_registry.provider.embed = counting_embed  # type: ignore[method-assign]

    comp = await _build(
        tmp_db, tmp_path,
        embedding_registry=embed_registry,
        provider_registry=provider_registry,
        lessons_index=lessons_index,
    )

    # "stale" unchanged -> every pass skipped it (no re-embed/re-summarize/re-publish).
    assert embed_calls == [1]  # only "fresh" embedded
    assert provider_registry.provider.calls == 2  # +1, only for "fresh"
    assert lessons_index.calls == 2  # +1 publish_many call, only "fresh" in the batch
    assert len(lessons_index.published[-1]) == 1

    fresh = await comp.store.get("user", "fresh")
    stale = await comp.store.get("user", "stale")
    assert fresh is not None and fresh.embedding is not None
    assert fresh.summary == "Do X. Then Y."
    assert stale is not None and stale.embedding is not None
    assert stale.summary == "Do X. Then Y."
