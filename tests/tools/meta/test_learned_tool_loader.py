"""LearnedToolLoader — boot-time reload of agent-authored tool specs.

Mirrors the SkillLoader self-heal contract: a corrupt / schema-invalid spec on
disk is logged and SKIPPED, the valid one is registered, and load_all NEVER
raises (a broken file can't wedge boot). This protects the T2 reboot-reload path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackowl.paths import StackowlHome
from stackowl.tools.meta.learned_tool_loader import LearnedToolLoader
from stackowl.tools.registry import ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


def _valid_spec_json(name: str = "shout") -> str:
    return json.dumps(
        {
            "name": name,
            "description": "uppercase a string via tr",
            "params": [{"name": "text", "type": "string", "description": "the text", "required": True}],
            "argv_template": ["tr", "a-z", "A-Z"],
            "action_severity": "read",
        }
    )


async def test_load_all_registers_valid_skips_corrupt_and_invalid(tmp_home: Path) -> None:
    learned_dir = StackowlHome.learned_tools_dir()
    learned_dir.mkdir(parents=True, exist_ok=True)
    # 1) a valid spec
    (learned_dir / "shout.json").write_text(_valid_spec_json(), encoding="utf-8")
    # 2) a corrupt (non-JSON) file
    (learned_dir / "broken.json").write_text("{ this is not json", encoding="utf-8")
    # 3) a schema-valid JSON but spec-invalid (argv[0] is a placeholder)
    (learned_dir / "evil.json").write_text(
        json.dumps(
            {
                "name": "evil",
                "description": "argv0 placeholder",
                "params": [{"name": "p", "type": "string", "description": "p", "required": True}],
                "argv_template": ["{p}", "x"],
            }
        ),
        encoding="utf-8",
    )

    registry = ToolRegistry()
    count = await LearnedToolLoader().load_all(registry)

    assert count == 1
    assert registry.get("shout") is not None
    assert registry.get("evil") is None
    assert registry.get("broken") is None


async def test_load_all_empty_dir_returns_zero_never_raises(tmp_home: Path) -> None:
    registry = ToolRegistry()
    count = await LearnedToolLoader().load_all(registry)
    assert count == 0
