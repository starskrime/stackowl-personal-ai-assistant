"""LAT.5 — SkillsAssembly's enrichment passes must not gate boot-readiness.

``load_only()`` returns as soon as skills are scanned off disk (no embedding
I/O); ``enrich()`` runs the back-fill passes and is what the boot call site
(startup/orchestrator.py) fires as a background ``asyncio.create_task`` after
the gateway is serving turns, rather than awaiting inline.

The gate moved from the LLM provider to the EMBEDDING provider in D09.3 slice 5.
It had to: summarization was the only enrich pass that called an LLM, and with
that pass removed a gated provider gates nothing — the task completed instantly
and the test asserted a property it was no longer exercising. Embedding is now
the slow pass, so that is what has to be held open to observe "boot proceeded
while enrichment is still in flight".
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from stackowl.owls.registry import OwlRegistry
from stackowl.skills.assembly import SkillsAssembly
from stackowl.tools.registry import ToolRegistry


@dataclass
class _GatedEmbedder:
    """An embedding provider whose ``embed()`` blocks until released — stands in
    for the real round-trip so the test can observe "boot proceeded while
    enrichment is still in flight" deterministically."""

    gate: asyncio.Event
    calls: int = 0
    #: On the PROVIDER, not the registry — that is where _embed_missing reads it.
    model_name: str = "stub-embed-v1"

    async def embed(self, texts):  # noqa: ANN001
        self.calls += 1
        await self.gate.wait()
        return [[0.1, 0.2] for _ in texts]


class _StubEmbeddingRegistry:
    def __init__(self, provider: _GatedEmbedder) -> None:
        self.provider = provider

    def get(self) -> _GatedEmbedder:
        return self.provider


def _write_skill(root: Path, name: str, *, body: str = "long body to summarize") -> None:
    d = root / "user" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\n{body}\n", encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_load_only_returns_without_running_enrichment(tmp_db, tmp_path: Path) -> None:
    """load_only() must not touch the provider registry at all — it isn't even
    a parameter — proving skill *loading* (what the platform needs to know
    what skills exist) is independent of the enrichment passes."""
    _write_skill(tmp_path, "alpha")

    components = await SkillsAssembly.load_only(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=tmp_path, builtin_seed_dir=tmp_path / "none",
    )

    assert len(components.loaded) == 1
    sk = await components.store.get("user", "alpha")
    assert sk is not None
    assert sk.embedding is None  # enrichment never ran


@pytest.mark.asyncio
async def test_enrich_runs_as_backgroundable_task_without_blocking_caller(
    tmp_db, tmp_path: Path,
) -> None:
    """Boot-readiness proof: fire enrich() via asyncio.create_task (mirroring
    the orchestrator's boot call site) and confirm the caller regains control
    immediately — the task is still pending (embedding gated) while other boot
    work proceeds unblocked. Only after the gate is released does it land."""
    _write_skill(tmp_path, "alpha")
    components = await SkillsAssembly.load_only(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=tmp_path, builtin_seed_dir=tmp_path / "none",
    )

    gate = asyncio.Event()
    embedder = _GatedEmbedder(gate=gate)
    task = asyncio.create_task(
        SkillsAssembly.enrich(
            components, embedding_registry=_StubEmbeddingRegistry(embedder),
        ),
    )
    # Wait until the task actually reaches the gated embed() call. A single
    # sleep(0) is not enough — enrich has real DB awaits ahead of the gate — and
    # asserting "not done" before the work has started proves nothing at all.
    for _ in range(200):
        if embedder.calls:
            break
        await asyncio.sleep(0.01)
    assert embedder.calls == 1, "enrich never reached the gated embedding pass"

    # "Boot proceeds" — the platform can keep doing other things right now;
    # the enrichment task has not completed and holds no lock the caller needs.
    assert not task.done()
    sk_mid_flight = await components.store.get("user", "alpha")
    assert sk_mid_flight.embedding is None  # not written yet — pass still in flight

    gate.set()
    await task

    sk_final = await components.store.get("user", "alpha")
    assert sk_final.embedding is not None
