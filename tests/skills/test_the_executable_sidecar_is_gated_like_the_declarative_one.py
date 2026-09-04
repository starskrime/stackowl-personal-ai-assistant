"""A skill's `owls.yaml` was trusted by SOURCE. Its `tools/*.py` was not.

MEASURED 2026-09-04, working D10.4. The map said "no install path from outside".
That is wrong: `/skill add <local-path>` and `/skill add --url <url>` both exist
and are user-reachable, installing from a directory, a `git clone --depth=1`, or
a downloaded archive (with real defences against oversized downloads and zip
bombs) into `skills/installed/`.

What the loader then does with what arrived was asymmetric:

    owls.yaml     — DECLARATIVE — `if source in _OWL_TRUSTED_SOURCES`
    tools/*.py    — EXECUTED    — `if tools_dir.exists()`

Every `.py` under `tools/` is imported with `exec_module` and its `Tool`
subclasses instantiated and registered. So the declarative sidecar was gated on
where the skill came from, and the executable one was not gated on that at all.

THERE IS A GUARD, AND IT ANSWERS A DIFFERENT QUESTION. D05.1's actuator refuses
to exec skill tool modules when the skills tree sits INSIDE the model-writable
workspace. That is about whether the MODEL can write what we execute. It says
nothing about whether an arbitrary git URL can, and for `installed/` it is
explicitly a no-op — its own comment says "normally a no-op: the two trees are
siblings under ~/.stackowl".

Nor does a scan cover it. `gated_skill_write` runs `security_scan_gate` HARD and
fails closed — but only on the agent's own authoring path, and only over the
SKILL.md TEXT. `skill_helpers.py`, which is what `/skill add` uses, references
the scan gate ZERO times.

The fix is the one already in the file: gate the executable sidecar on the same
trust set as the declarative one, and say so in the log rather than dropping it
silently. Trusted is `{builtin, user}` — code we shipped, or code the operator
placed by hand. Not `learned` (the model wrote it) and not `installed` (a URL
did).

ZERO LIVE IMPACT, measured before changing it: all 39 registered skills report
`tools: 0` and there is not one `tools/` directory anywhere under
`~/.stackowl/skills`. Whether an installed skill should EVER ship executable
tools, behind what scan, is a risk-appetite question for the operator — ESC-129.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from stackowl.skills.loader import _TRUSTED_EXTENSION_SOURCES, SkillLoader
from stackowl.skills.manifest import SkillSource
from stackowl.tools.registry import ToolRegistry

_TOOL_PY = '''
from stackowl.tools.base import Tool, ToolManifest, ToolResult


class PlantedTool(Tool):
    @property
    def name(self): return "planted_tool"
    @property
    def description(self): return "d"
    @property
    def parameters(self): return {"type": "object", "properties": {}}
    @property
    def manifest(self):
        return ToolManifest(name="planted_tool", description="d",
                            parameters={"type": "object", "properties": {}},
                            action_severity="read")
    async def execute(self, **kw):
        return ToolResult(success=True, output="ok", duration_ms=0.0)
'''


def _skill_with_a_tool(root: Path, source: str, name: str) -> None:
    d = root / source / name
    (d / "tools").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    (d / "tools" / "planted.py").write_text(_TOOL_PY, encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["installed", "learned"])
async def test_an_untrusted_source_cannot_register_executable_tools(
    tmp_path: Path, caplog, source: str
) -> None:
    """`installed` arrives from a URL; `learned` is written by the model. Neither
    is a source we execute Python from."""
    _skill_with_a_tool(tmp_path, source, "planted")
    registry = ToolRegistry()

    loaded = await SkillLoader(tool_registry=registry).load_all(
        tmp_path, builtin_seed_dir=tmp_path / "none"
    )

    assert [ls.manifest.name for ls in loaded] == ["planted"], (
        "the SKILL itself still loads — only its executable sidecar is refused"
    )
    assert loaded[0].tool_names == ()
    assert registry.source_of("planted_tool") is None, (
        "a tool from an untrusted source must never reach the registry"
    )
    assert any(
        "refusing tools" in r.getMessage() for r in caplog.records
    ), "a refusal nobody can see is indistinguishable from a skill that had no tools"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["builtin", "user"])
async def test_a_trusted_source_still_registers_its_tools(
    tmp_path: Path, source: str
) -> None:
    """The control. A gate that refuses everything has not been shown to work,
    and `builtin` shipping tools is the whole point of the extension point."""
    _skill_with_a_tool(tmp_path, source, "shipped")
    registry = ToolRegistry()

    loaded = await SkillLoader(tool_registry=registry).load_all(
        tmp_path, builtin_seed_dir=tmp_path / "none"
    )

    assert loaded[0].tool_names == ("planted_tool",)
    assert registry.source_of("planted_tool") == "shipped"


def test_both_sidecars_read_ONE_trust_set() -> None:
    """They were separate constants for the same rule, which is how they came to
    disagree in the first place. One definition, or they drift again."""
    from stackowl.skills import loader

    assert loader._OWL_TRUSTED_SOURCES is _TRUSTED_EXTENSION_SOURCES
    assert _TRUSTED_EXTENSION_SOURCES == frozenset({"builtin", "user"})
    assert _TRUSTED_EXTENSION_SOURCES < set(get_args(SkillSource)), (
        "the trusted set must be a strict subset of the sources, or the gate is "
        "decorative"
    )
