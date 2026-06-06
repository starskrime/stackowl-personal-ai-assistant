from dataclasses import dataclass, field
from pathlib import Path

import pytest

from stackowl.owls.registry import OwlRegistry
from stackowl.skills.assembly import SkillsAssembly
from stackowl.tools.registry import ToolRegistry


@dataclass
class _StubProvider:
    out: str = "Do X. Then Y."
    calls: int = 0
    async def complete(self, messages, **kw):
        self.calls += 1
        class _R:
            content = ""
        r = _R(); r.content = self.out; return r


class _StubProviderRegistry:
    def __init__(self, provider): self._p = provider
    def get_with_cascade(self, tier): return self._p


def _write(root: Path, name="alpha", body="long body to summarize", summary=None):
    d = root / "user" / name
    d.mkdir(parents=True)
    fm = f"---\nname: {name}\ndescription: d\n"
    if summary is not None:
        fm += f"summary: {summary}\n"
    fm += f"---\n{body}\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")


async def _build(tmp_db, root, provider):
    return await SkillsAssembly.build(
        db=tmp_db, tool_registry=ToolRegistry(), owl_registry=OwlRegistry(),
        skills_root=root, builtin_seed_dir=root / "none",
        provider_registry=_StubProviderRegistry(provider),
    )


@pytest.mark.asyncio
async def test_generates_summary_when_missing(tmp_db, tmp_path: Path):
    _write(tmp_path)
    prov = _StubProvider()
    comp = await _build(tmp_db, tmp_path, prov)
    sk = await comp.store.get("user", "alpha")
    assert sk.summary == "Do X. Then Y."
    assert sk.summary_source == "generated"
    assert prov.calls == 1


@pytest.mark.asyncio
async def test_skips_when_summary_present_and_hash_matches(tmp_db, tmp_path: Path):
    _write(tmp_path)
    prov = _StubProvider()
    await _build(tmp_db, tmp_path, prov)      # generates (1 call)
    await _build(tmp_db, tmp_path, prov)      # reboot, unchanged body → no new call
    assert prov.calls == 1


@pytest.mark.asyncio
async def test_empty_output_leaves_summary_null(tmp_db, tmp_path: Path):
    _write(tmp_path)
    comp = await _build(tmp_db, tmp_path, _StubProvider(out="   "))
    sk = await comp.store.get("user", "alpha")
    assert sk.summary is None


@pytest.mark.asyncio
async def test_author_summary_never_overwritten_by_generation(tmp_db, tmp_path: Path):
    # an authored summary must never be regenerated/overwritten by the back-fill
    _write(tmp_path, summary="AUTHORED PLAYBOOK")
    prov = _StubProvider(out="GENERATED — should not appear")
    comp = await _build(tmp_db, tmp_path, prov)
    sk = await comp.store.get("user", "alpha")
    assert sk.summary == "AUTHORED PLAYBOOK"
    assert sk.summary_source == "author"
    assert prov.calls == 0  # provider never invoked for an authored skill
