"""LAT.5 — SkillsAssembly.build()'s enrichment passes (embed/summarize/publish)
must not gate boot-readiness. ``load_only()`` returns as soon as skills are
scanned off disk (no LLM/embedding I/O); ``enrich()`` runs the three passes and
is the piece the boot call site (startup/orchestrator.py) now fires as a
background ``asyncio.create_task`` after the gateway is serving turns instead
of awaiting inline. ``build()`` itself keeps the old fully-synchronous
behavior (load_only + enrich awaited back to back) for callers that want it
(tests, non-boot assembly wiring) — see tests/skills/test_summarize_backfill.py
and tests/skills/test_assembly_batch_reads.py, unchanged by this story.
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
class _GatedProvider:
    """A summarize provider whose ``complete()`` blocks until released —
    stands in for the ~1-2s real LLM round-trip so the test can observe
    "boot proceeded while enrichment is still in flight" deterministically."""

    gate: asyncio.Event
    calls: int = 0

    async def complete(self, messages, **kw):  # noqa: ANN001, ANN003
        self.calls += 1
        await self.gate.wait()

        class _R:
            content = "Do X. Then Y."

        return _R()


class _StubProviderRegistry:
    def __init__(self, provider: _GatedProvider) -> None:
        self._p = provider

    def get_with_cascade(self, tier: str) -> tuple[_GatedProvider, str]:  # noqa: ARG002
        return self._p, ""


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
    assert sk.summary is None  # enrichment never ran


@pytest.mark.asyncio
async def test_enrich_runs_as_backgroundable_task_without_blocking_caller(
    tmp_db, tmp_path: Path,
) -> None:
    """Boot-readiness proof: fire enrich() via asyncio.create_task (mirroring
    the orchestrator's boot call site) and confirm the caller regains control
    immediately — the task is still pending (LLM call gated) while other boot
    work (represented here by a plain assertion) proceeds unblocked. Only
    after the gate is released does the summary land."""
    _write_skill(tmp_path, "alpha")
    components = await SkillsAssembly.load_only(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=tmp_path, builtin_seed_dir=tmp_path / "none",
    )

    gate = asyncio.Event()
    provider = _GatedProvider(gate=gate)
    task = asyncio.create_task(
        SkillsAssembly.enrich(components, provider_registry=_StubProviderRegistry(provider)),
    )
    # Yield once so the task starts and reaches the gated provider.complete().
    await asyncio.sleep(0)

    # "Boot proceeds" — the platform can keep doing other things right now;
    # the enrichment task has not completed and holds no lock the caller needs.
    assert not task.done()
    sk_mid_flight = await components.store.get("user", "alpha")
    assert sk_mid_flight.summary is None  # not written yet — pass still in flight

    gate.set()
    await task

    sk_final = await components.store.get("user", "alpha")
    assert sk_final.summary == "Do X. Then Y."
