"""PLUG-3 / F047 — authored-skill tool re-registration: idempotent re-load vs collision.

``reindex_after_change`` re-runs ``loader.load_all`` over the WHOLE skills tree, so
every already-registered skill's tool is registered a SECOND time. Previously
``_load_tools`` registered without ``replace=True`` (ToolRegistrationError "already
registered") and only best-effort logged it — so a re-authored learned skill
silently kept its STALE tool and the user got a generic "reindex pending" note that
hid the real cause (F047).

Root-cause fix:
  * Re-registering a tool already owned by the SAME source is an INTENTIONAL update
    (re-author / idempotent reindex) → ``replace=True``, succeeds cleanly.
  * A tool name owned by a DIFFERENT source is a genuine COLLISION → a distinct
    ``ToolRegistrationError`` whose message names the conflicting owner, NOT a
    silent skip and NOT a misleading "reindex pending".
  * A dangerous/consequential collision is STILL refused (consent boundary intact).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.exceptions import ToolRegistrationError
from stackowl.owls.registry import OwlRegistry
from stackowl.skills.loader import SkillLoader
from stackowl.tools.registry import ToolRegistry

_TOOL_TMPL = (
    "from stackowl.tools.base import Tool, ToolManifest, ToolResult\n"
    "class T(Tool):\n"
    "    @property\n    def name(self): return {tool!r}\n"
    "    @property\n    def description(self): return 'd'\n"
    "    @property\n    def parameters(self): return {{'type':'object','properties':{{}}}}\n"
    "    @property\n    def manifest(self):\n"
    "        return ToolManifest(name={tool!r}, description='d',"
    " parameters={{'type':'object','properties':{{}}}}, action_severity='read')\n"
    "    async def execute(self, **kw):\n"
    "        return ToolResult(success=True, output='ok', duration_ms=0.0)\n"
)


def _write_skill(root: Path, source: str, skill: str, tool: str) -> None:
    d = root / source / skill
    (d / "tools").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    (d / "tools" / "t.py").write_text(_TOOL_TMPL.format(tool=tool), encoding="utf-8")


@pytest.mark.asyncio
async def test_reload_same_skill_tool_is_idempotent(tmp_path: Path) -> None:
    """Re-loading the same skill (the reindex case) must not raise — replace=True."""
    _write_skill(tmp_path, "learned", "myskill", "my_tool")
    reg = ToolRegistry()
    loader = SkillLoader(tool_registry=reg, owl_registry=OwlRegistry())
    await loader.load_all(tmp_path, builtin_seed_dir=tmp_path / "none")
    assert reg.get("my_tool") is not None
    # Second load (what reindex_after_change does) must succeed cleanly.
    loaded = await loader.load_all(tmp_path, builtin_seed_dir=tmp_path / "none")
    sk = next(ls for ls in loaded if ls.manifest.name == "myskill")
    assert sk.tool_names == ("my_tool",)
    assert reg.get("my_tool") is not None


@pytest.mark.asyncio
async def test_cross_skill_collision_raises_distinct_error(tmp_path: Path) -> None:
    """Two DIFFERENT skills declaring the same tool name → distinct collision error."""
    _write_skill(tmp_path, "user", "skill_a", "shared_tool")
    reg = ToolRegistry()
    loader = SkillLoader(tool_registry=reg, owl_registry=OwlRegistry())
    await loader.load_all(tmp_path, builtin_seed_dir=tmp_path / "none")
    # A second, DIFFERENT skill claims the same tool name.
    _write_skill(tmp_path, "learned", "skill_b", "shared_tool")
    with pytest.raises(ToolRegistrationError) as ei:
        loader._load_tools(tmp_path / "learned" / "skill_b" / "tools", "skill_b")
    # Distinct, owner-naming message — NOT a generic "reindex pending".
    msg = str(ei.value).lower()
    assert "collision" in msg or "owned by" in msg
    assert "skill_a" in str(ei.value)
