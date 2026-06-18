"""Tests for Learning Commit 3 sub-phase 3d — skill embedding + semantic recall
+ classify.py integration."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path

from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.classify import _gather_relevant_skills
from stackowl.skills.assembly import SkillsAssembly
from stackowl.tools.registry import ToolRegistry

# ---------- in-memory embedding registry stub ------------------------------


@dataclass
class _StubEmbeddingProvider:
    """Maps any input text deterministically to a fixed-dim vector.

    Hashes the input modulo a small basis so unit tests can predict similarity
    without booting a real model.
    """

    dim: int = 8
    model_name: str = "stub-embed-v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                # Use deterministic hash (sha1) instead of builtin hash(),
                # which is randomly seeded per-process and made the test flaky
                # under different test orderings.
                digest = hashlib.sha1(tok.encode("utf-8")).digest()
                bucket = digest[0] % self.dim
                vec[bucket] += 1.0
            # L2-normalize so cosine and dot product line up.
            n = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / n for x in vec])
        return out


@dataclass
class _StubEmbeddingRegistry:
    provider: _StubEmbeddingProvider = field(default_factory=_StubEmbeddingProvider)

    def get(self) -> _StubEmbeddingProvider:
        return self.provider


# ---------- assembly embed-on-boot pass ------------------------------------


def _write_skill_md(dir_: Path, name: str, *, description: str, body: str) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


async def test_assembly_embeds_all_loaded_skills(tmp_db: DbPool, tmp_path: Path) -> None:
    skills_root = tmp_path / "ws" / "skills"
    _write_skill_md(skills_root / "user" / "alpha", name="alpha",
                    description="alpha skill about a thing", body="alpha body")
    _write_skill_md(skills_root / "user" / "beta", name="beta",
                    description="beta skill about another thing", body="beta body")
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
        embedding_registry=_StubEmbeddingRegistry(),
    )
    # Every loaded skill should have an embedding after build().
    a = await components.store.get("user", "alpha")
    b = await components.store.get("user", "beta")
    assert a is not None and b is not None
    assert a.embedding is not None and len(a.embedding) == 8
    assert b.embedding is not None and len(b.embedding) == 8
    assert a.embedding_model == "stub-embed-v1"


async def test_assembly_skips_embed_when_model_unchanged(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """Re-running build() with the same embedding model must not re-embed."""
    skills_root = tmp_path / "ws" / "skills"
    _write_skill_md(skills_root / "user" / "skill", name="skill",
                    description="d", body="b")
    registry = _StubEmbeddingRegistry()

    # Count embed calls.
    calls: list[int] = []
    orig = registry.provider.embed

    async def counting_embed(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return await orig(texts)

    registry.provider.embed = counting_embed  # type: ignore[method-assign]

    await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
        embedding_registry=registry,
    )
    first_calls = list(calls)
    await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
        embedding_registry=registry,
    )
    # First build embedded once; second build should have done nothing new.
    assert first_calls == [1]
    assert calls == [1]


# ---------- semantic_recall directly --------------------------------------


async def test_semantic_recall_returns_highest_similarity(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    skills_root = tmp_path / "ws" / "skills"
    _write_skill_md(skills_root / "user" / "pdf", name="pdf",
                    description="summarize pdfs", body="chunk and recurse")
    _write_skill_md(skills_root / "user" / "shell", name="shell",
                    description="run shell commands", body="bash exec")
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
        embedding_registry=_StubEmbeddingRegistry(),
    )
    # Query the embed provider directly so we can pass the resulting vector.
    embedder = _StubEmbeddingRegistry()
    [qvec] = await embedder.provider.embed(["summarize pdfs chunk"])
    hits = await components.store.semantic_recall(qvec, limit=2)
    assert len(hits) >= 1
    # The pdf skill should be the top hit.
    assert hits[0][0].name == "pdf"
    assert hits[0][1] > 0.0


async def test_semantic_recall_skips_disabled_skills(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    skills_root = tmp_path / "ws" / "skills"
    _write_skill_md(skills_root / "user" / "a", name="a",
                    description="alpha", body="x")
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
        embedding_registry=_StubEmbeddingRegistry(),
    )
    sk = await components.store.get("user", "a")
    assert sk is not None
    await components.store.set_enabled(sk.skill_id, enabled=False)
    [qvec] = await _StubEmbeddingProvider().embed(["alpha"])
    assert await components.store.semantic_recall(qvec, limit=5) == []


async def test_semantic_recall_returns_empty_when_no_embeddings(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """A skill without an embedding column populated is silently skipped."""
    skills_root = tmp_path / "ws" / "skills"
    _write_skill_md(skills_root / "user" / "no-embed", name="no-embed",
                    description="x", body="y")
    # NOTE: no embedding_registry passed → assembly skips the embed pass.
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
    )
    [qvec] = await _StubEmbeddingProvider().embed(["anything"])
    assert await components.store.semantic_recall(qvec, limit=5) == []


# ---------- classify._gather_relevant_skills wired via StepServices --------


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t", session_id="s", input_text="how do i summarize a pdf",
        channel="cli", owl_name="scout", pipeline_step="start",
    )


async def test_gather_relevant_skills_returns_block_when_wired(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    skills_root = tmp_path / "ws" / "skills"
    _write_skill_md(skills_root / "user" / "pdf", name="pdf",
                    description="summarize pdfs", body="chunk and recurse")
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
        embedding_registry=_StubEmbeddingRegistry(),
    )
    services = StepServices(
        skill_store=components.store,
        embedding_registry=_StubEmbeddingRegistry(),
    )
    token = set_services(services)
    try:
        block = await _gather_relevant_skills("summarize pdfs", limit=3)
    finally:
        reset_services(token)
    assert "## Relevant Skills" in block
    assert "**pdf**" in block
    assert "summarize pdfs" in block
    assert "/skill show" in block


async def test_gather_relevant_skills_returns_empty_when_unwired() -> None:
    """No skill_store wired → returns "" so classify falls through."""
    token = set_services(StepServices())  # everything None
    try:
        block = await _gather_relevant_skills("anything", limit=3)
    finally:
        reset_services(token)
    assert block == ""


async def test_gather_relevant_skills_returns_empty_when_no_skills_match(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """No qualifying skills (no embeddings) → empty block, classify continues."""
    skills_root = tmp_path / "ws" / "skills"
    skills_root.mkdir(parents=True)
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
        embedding_registry=_StubEmbeddingRegistry(),
    )
    services = StepServices(
        skill_store=components.store,
        embedding_registry=_StubEmbeddingRegistry(),
    )
    token = set_services(services)
    try:
        block = await _gather_relevant_skills("anything", limit=3)
    finally:
        reset_services(token)
    assert block == ""


async def test_gather_relevant_skills_respects_limit(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    skills_root = tmp_path / "ws" / "skills"
    for i in range(5):
        _write_skill_md(skills_root / "user" / f"s{i}", name=f"s{i}",
                        description=f"skill number {i}", body="b")
    components = await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=skills_root, builtin_seed_dir=tmp_path / "no_builtins",
        embedding_registry=_StubEmbeddingRegistry(),
    )
    services = StepServices(
        skill_store=components.store,
        embedding_registry=_StubEmbeddingRegistry(),
    )
    token = set_services(services)
    try:
        block = await _gather_relevant_skills("skill", limit=2)
    finally:
        reset_services(token)
    # Header + footer + 2 hits = 4 lines.
    assert block.count("**s") == 2
