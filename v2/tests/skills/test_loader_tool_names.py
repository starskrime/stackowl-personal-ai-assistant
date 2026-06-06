from pathlib import Path

import pytest

from stackowl.owls.registry import OwlRegistry
from stackowl.skills.loader import SkillLoader
from stackowl.tools.registry import ToolRegistry


def _write_skill_with_tool(root: Path):
    d = root / "user" / "withtool"
    (d / "tools").mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: withtool\ndescription: d\n---\nbody\n", encoding="utf-8")
    (d / "tools" / "mytool.py").write_text(
        "from stackowl.tools.base import Tool, ToolManifest, ToolResult\n"
        "class MyTool(Tool):\n"
        "    @property\n    def name(self): return 'my_skill_tool'\n"
        "    @property\n    def description(self): return 'd'\n"
        "    @property\n    def parameters(self): return {'type':'object','properties':{}}\n"
        "    @property\n    def manifest(self):\n"
        "        return ToolManifest(name='my_skill_tool', description='d',"
        " parameters={'type':'object','properties':{}}, action_severity='read')\n"
        "    async def execute(self, **kw):\n"
        "        return ToolResult(success=True, output='ok', duration_ms=0.0)\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_loader_captures_tool_names(tmp_path: Path):
    _write_skill_with_tool(tmp_path)
    loader = SkillLoader(tool_registry=ToolRegistry(), owl_registry=OwlRegistry())
    loaded = await loader.load_all(tmp_path, builtin_seed_dir=tmp_path / "none")
    sk = next(ls for ls in loaded if ls.manifest.name == "withtool")
    assert sk.tool_names == ("my_skill_tool",)


@pytest.mark.asyncio
async def test_zero_tool_skill_has_empty_tool_names(tmp_path: Path):
    d = tmp_path / "user" / "notool"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: notool\ndescription: d\n---\nb\n", encoding="utf-8")
    loader = SkillLoader(tool_registry=ToolRegistry(), owl_registry=OwlRegistry())
    loaded = await loader.load_all(tmp_path, builtin_seed_dir=tmp_path / "none")
    sk = next(ls for ls in loaded if ls.manifest.name == "notool")
    assert sk.tool_names == ()
