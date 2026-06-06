"""Security gate: skill packages from untrusted sources must not register owls.

Only 'builtin' and 'user' sources are trusted for owl registration.
'installed' and 'learned' sources are untrusted (third-party / auto-acquired).
"""

from pathlib import Path

import pytest

from stackowl.owls.registry import OwlRegistry
from stackowl.skills.loader import SkillLoader
from stackowl.tools.registry import ToolRegistry


def _skill_with_owls(root: Path, source: str, owl_name: str) -> None:
    d = root / source / "pkg"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: pkg\ndescription: d\n---\nb\n", encoding="utf-8")
    (d / "owls.yaml").write_text(
        f"- name: {owl_name}\n  role: r\n  system_prompt: p\n  model_tier: fast\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_installed_skill_cannot_register_owl(tmp_path: Path):
    _skill_with_owls(tmp_path, "installed", "evil_owl")
    reg = OwlRegistry.with_default_secretary()
    loader = SkillLoader(tool_registry=ToolRegistry(), owl_registry=reg)
    await loader.load_all(tmp_path, builtin_seed_dir=tmp_path / "none")
    assert "evil_owl" not in [m.name for m in reg.list()]


@pytest.mark.asyncio
async def test_learned_skill_cannot_register_owl(tmp_path: Path):
    _skill_with_owls(tmp_path, "learned", "evil_owl2")
    reg = OwlRegistry.with_default_secretary()
    loader = SkillLoader(tool_registry=ToolRegistry(), owl_registry=reg)
    await loader.load_all(tmp_path, builtin_seed_dir=tmp_path / "none")
    assert "evil_owl2" not in [m.name for m in reg.list()]


@pytest.mark.asyncio
async def test_user_skill_can_register_owl(tmp_path: Path):
    _skill_with_owls(tmp_path, "user", "ok_owl")
    reg = OwlRegistry.with_default_secretary()
    loader = SkillLoader(tool_registry=ToolRegistry(), owl_registry=reg)
    await loader.load_all(tmp_path, builtin_seed_dir=tmp_path / "none")
    assert "ok_owl" in [m.name for m in reg.list()]
