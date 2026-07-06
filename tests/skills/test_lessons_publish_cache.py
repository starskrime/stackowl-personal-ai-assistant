"""_publish_to_lessons must skip unchanged skills on repeat boots.

Root cause: unlike _summarize_missing, it had no cache gate and re-embedded
every loaded skill's content on every boot (~24s of local model inference for
~300 skills on this hardware, doubled by the gateway/core split). Mirrors
test_summarize_backfill.py's pattern for the sibling _summarize_missing gate.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from stackowl.owls.registry import OwlRegistry
from stackowl.skills.assembly import SkillsAssembly
from stackowl.tools.registry import ToolRegistry


@dataclass
class _StubLessonsIndex:
    calls: int = 0
    published: list = field(default_factory=list)

    async def publish_many(self, drafts):
        self.calls += 1
        self.published.append(list(drafts))
        return len(drafts)


def _write(root: Path, name: str = "alpha", body: str = "long body to publish") -> None:
    d = root / "user" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\n{body}\n", encoding="utf-8",
    )


async def _build(tmp_db, root, lessons_index):
    return await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=root, builtin_seed_dir=root / "none",
        lessons_index=lessons_index,
    )


@pytest.mark.asyncio
async def test_publishes_when_missing(tmp_db, tmp_path: Path):
    _write(tmp_path)
    idx = _StubLessonsIndex()
    await _build(tmp_db, tmp_path, idx)
    assert idx.calls == 1
    assert len(idx.published[0]) == 1


@pytest.mark.asyncio
async def test_skips_when_content_unchanged(tmp_db, tmp_path: Path):
    _write(tmp_path)
    idx = _StubLessonsIndex()
    await _build(tmp_db, tmp_path, idx)  # boot 1: publishes
    await _build(tmp_db, tmp_path, idx)  # boot 2: unchanged body → no re-publish
    assert idx.calls == 1


@pytest.mark.asyncio
async def test_republishes_when_body_changes(tmp_db, tmp_path: Path):
    _write(tmp_path, body="version one")
    idx = _StubLessonsIndex()
    await _build(tmp_db, tmp_path, idx)
    _write(tmp_path, body="version two — materially different content")
    await _build(tmp_db, tmp_path, idx)
    assert idx.calls == 2
