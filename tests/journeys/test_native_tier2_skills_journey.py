"""NATIVE TIER-2 SKILLS REACHABILITY JOURNEY — proves all 5 builtin skills load
and are discoverable through the real loader + store.

For each of the 5 skills this test asserts:
  (a) the skill loads from the real _builtin seed dir and validates (manifest
      parses, name matches directory), and
  (b) it is discoverable / surfaced through the real SkillsAssembly+SkillLoader
      (the "registered ≠ reachable" guard, not just a unit-parse check).

Uses the real SkillsAssembly.build() + the shipped _builtin seed dir — no mocks
of the manifest or loader.  A RecordingProvider run is omitted here because the
pure-loader reachability proof (load → list → found in store) is the cleanest
single-seam guard for the new skills, and the existing
test_skill_discovery_journey.py already covers the full dispatch path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.skills.assembly import SkillsAssembly
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.skill_md import parse_skill_md
from stackowl.tools.registry import ToolRegistry
from tests._support.skill_journey import StubEmbeddingRegistry

# The real on-disk builtin seed directory shipped with StackOwl.
_BUILTIN_SEED_DIR = (
    Path(__file__).parent.parent.parent
    / "src" / "stackowl" / "skills" / "_builtin"
)

# The 5 tier-2 skills this task introduces.
TIER2_SKILLS = [
    "web-automation",
    "memory-curation",
    "schedule-proactive",
    "delegate-or-debate",
    "document-extract",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_builtin_skill_md(name: str) -> tuple[SkillManifest, str]:
    """Parse one builtin SKILL.md and return (manifest, body)."""
    skill_dir = _BUILTIN_SEED_DIR / name
    skill_file = skill_dir / "SKILL.md"
    assert skill_file.exists(), f"Missing: {skill_file}"
    parsed = parse_skill_md(skill_file.read_text(encoding="utf-8"))
    manifest = SkillManifest(**{k: v for k, v in parsed.frontmatter.items() if k != "source"})
    return manifest, parsed.body


# ---------------------------------------------------------------------------
# (a) Manifest-parse + name-matches-dir, one test per skill
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("skill_name", TIER2_SKILLS)
def test_builtin_skill_manifest_parses_and_name_matches_dir(skill_name: str) -> None:
    """SKILL.md must parse cleanly and name must equal the directory name."""
    manifest, body = _read_builtin_skill_md(skill_name)
    assert manifest.name == skill_name, (
        f"Manifest name '{manifest.name}' != dir name '{skill_name}'"
    )
    assert manifest.description, "description must be non-empty"
    assert manifest.version == "0.1.0"
    assert manifest.enabled is True


@pytest.mark.parametrize("skill_name", TIER2_SKILLS)
def test_builtin_skill_body_has_required_sections(skill_name: str) -> None:
    """Every tier-2 skill body MUST have ## Steps, ## Verification, ## Pitfalls."""
    _, body = _read_builtin_skill_md(skill_name)
    for section in ("## Steps", "## Verification", "## Pitfalls"):
        assert section in body, (
            f"Skill '{skill_name}' is missing section '{section}' in body"
        )


# ---------------------------------------------------------------------------
# (b) Reachability: all 5 load through SkillsAssembly and are in the store
# ---------------------------------------------------------------------------

async def test_all_tier2_skills_reachable_through_assembly(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """All 5 builtin tier-2 skills must be discoverable via the real assembly."""
    skills_root = tmp_path / "skills"
    # Use the real _builtin seed dir; no other source dirs needed.
    components = await SkillsAssembly.build(
        db=tmp_db,
        tool_registry=ToolRegistry(),
        owl_registry=OwlRegistry(),
        skills_root=skills_root,
        builtin_seed_dir=_BUILTIN_SEED_DIR,
        embedding_registry=StubEmbeddingRegistry(),
    )
    loaded_names = {s.manifest.name for s in components.loaded}
    for skill_name in TIER2_SKILLS:
        assert skill_name in loaded_names, (
            f"Skill '{skill_name}' not found in assembly output. "
            f"Loaded: {sorted(loaded_names)}"
        )

    # Also verify they are listed in the store (the "registered ≠ reachable" guard).
    store = components.store
    builtin_in_store = await store.list_for_source("builtin")
    store_names = {s.name for s in builtin_in_store}
    for skill_name in TIER2_SKILLS:
        assert skill_name in store_names, (
            f"Skill '{skill_name}' loaded but NOT in store. "
            f"Store has: {sorted(store_names)}"
        )
