"""SKILL-DISCOVERY GATEWAY JOURNEY — the default owl, out of the box, can DISCOVER
and USE a skill it does not own.

This is the reachability proof the prior skill tests lacked: they stubbed the
provider and only ever tested an owl that ALREADY owned the skill, so they proved
*rendering*, never *reachability*. Here the owl is the shipped default Secretary
(no skill ownership rigging), the store is a REAL SkillsAssembly over an on-disk
*categorized* SKILL.md (so it also exercises the loader-recursion fix), and the
provider is a RECORDING provider that runs a real `skills_list → skill_view`
program against the real tool dispatcher. If `skills_list` is not in the roster
the Secretary is handed, or the skill is not discoverable through it, the test
fails — turning "registered ≠ reachable" into a hard failure at the seam.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.config.settings import Settings, SkillsSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from tests._support.skill_journey import (
    RecordingProvider,
    build_env,
    build_store,
    run_turn,
    write_skill_md,
)

_SKILL = "dl-video"


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


async def _discover_and_view(dispatch, provider) -> str:  # noqa: ANN001
    """Scripted model program: list skills, then view the discovered one."""
    # Reachability guard #1: the discovery tool must be in the roster the default
    # Secretary was handed. (Recorded for the test to assert; we also stop here so
    # the trace stays empty and the test fails loudly if it's missing.)
    if "skills_list" not in provider.presented_tool_names:
        return "skills_list not in roster"
    listing = await dispatch("skills_list", {})
    # Reachability guard #2: the installed skill must be discoverable in the listing.
    if _SKILL not in listing:
        return f"skill not discoverable: {listing!r}"
    body = await dispatch("skill_view", {"name": _SKILL})
    provider.viewed_body = body  # type: ignore[attr-defined]
    return "Loaded the skill and proceeding."


async def test_default_secretary_discovers_and_uses_a_categorized_skill(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    skills_root = tmp_path / "ws" / "skills"
    # A CATEGORIZED (nested) skill — invisible before the loader-recursion fix.
    write_skill_md(
        skills_root, "learned", _SKILL, category="media",
        description="download a video from a URL",
        when_to_use="when the user wants to save a video",
        summary="resolve the media URL, download the stream, save to downloads/",
        body="Step 1. Resolve the URL. Step 2. Download. Step 3. Save to downloads/.",
    )
    store = await build_store(tmp_db, skills_root)

    # The shipped default owl — owns NO skills. No rigging.
    owl_registry = OwlRegistry.with_default_secretary()

    provider = RecordingProvider("secretary", _discover_and_view)
    env = build_env(provider, skill_store=store, owl_registry=owl_registry, settings=Settings())

    reply = await run_turn(env, "can you download a video for me?")

    # 1) AWARENESS: MY global CATALOG region reached the default owl's prompt
    # (distinct from classify's "## Relevant Skills" block — assert the catalog
    # header specifically so this proves the global-catalog feature, not recall).
    assert "## CATALOG" in provider.system_text, (
        "default Secretary never got the global skills CATALOG — global catalog not "
        f"injected into the system prompt. system_text={provider.system_text!r}"
    )
    assert _SKILL in provider.system_text
    # 2) REACHABILITY: the discovery tools survived the budget into the roster.
    assert "skills_list" in provider.presented_tool_names
    assert "skill_view" in provider.presented_tool_names
    # 3) END-TO-END: the model actually discovered then loaded the skill.
    assert provider.trace == ["skills_list", "skill_view"], (
        f"expected discover→view trace; got {provider.trace}"
    )
    assert "Step 1. Resolve the URL" in getattr(provider, "viewed_body", ""), (
        "skill_view did not return the real on-disk skill body"
    )
    assert reply  # the turn delivered a final reply


async def test_off_flag_default_owl_sees_no_catalog(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """With skills.global_catalog OFF, the default owl gets no catalog (baseline)."""
    skills_root = tmp_path / "ws" / "skills"
    write_skill_md(
        skills_root, "learned", _SKILL, category="media",
        description="download a video from a URL",
        summary="download and save a video",
    )
    store = await build_store(tmp_db, skills_root)
    owl_registry = OwlRegistry.with_default_secretary()

    async def _noop(dispatch, provider):  # noqa: ANN001
        return "hi"

    provider = RecordingProvider("secretary", _noop)
    # NOTE: Settings.settings_customise_sources drops init_settings, so constructor
    # kwargs are ignored — override via model_copy to actually flip the flag.
    settings = Settings().model_copy(update={"skills": SkillsSettings(global_catalog=False)})
    env = build_env(provider, skill_store=store, owl_registry=owl_registry, settings=settings)

    await run_turn(env, "can you download a video for me?")
    # The global CATALOG must be absent (classify's relevance block may still
    # surface a relevant skill — that's a different, pre-existing path).
    assert "## CATALOG" not in provider.system_text
