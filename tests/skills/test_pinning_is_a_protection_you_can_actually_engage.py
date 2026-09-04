"""`pinned` had three readers and no writer. The catalogue promised it anyway.

MEASURED 2026-09-04, working D10.7. The map calls skill ownership AHEAD on the
strength of three things — `owls/skill_ownership.py`, per-owl `tool_presets`, and
**pinned skills**. Two of the three are genuinely exercised: 29 `skill_ownership`
rows across 6 owls, and `tool_presets` is imported by six modules including
`authz_compose`. The third could never be turned on.

`pinned` is read in three places, and each reader protects something real:

    store.set_lifecycle_state   `... WHERE skill_id = ? AND owner_id = ? AND pinned = 0`
    consolidation.py            pinned members of a merge cluster, twice
    lifecycle.py:218            `if row.pinned`

`store.set_pinned` exists. Its callers: SEVEN, and every one of them is a test.
Zero in `src/`. The empirical check agrees independently of any grep — **0 of 39
skills are pinned**, and that number could not change.

WHAT MAKES IT MORE THAN A DORMANT FLAG: `/skill dedupe`'s own help text tells the
operator "a pinned member wins outright". A user-facing command documented a
lever that did not exist. That is the `/quiet` shape from D15.6 inverted — there,
a command recorded an override nothing read; here, a command promises a mechanism
nothing can set.

It is also the lever ESC-128 needs. That escalation asks whether to merge ten
duplicate learned skills, and the merge path reads `pinned` to decide which
member survives. Answering it safely means being able to say "keep this one".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore


def _loaded(name: str) -> LoadedSkill:
    return LoadedSkill(
        manifest=SkillManifest(name=name, description="d", source="learned"),
        path=Path("/tmp/x"), body="b", tools_registered=0, owls_registered=0, tool_names=(),
    )


@pytest.mark.asyncio
async def test_an_unpinned_skill_can_still_be_retired(tmp_db) -> None:
    """The control. If retirement stopped working the pin would look effective
    for the wrong reason."""
    store = SkillIndexStore(tmp_db)
    sid = await store.upsert(_loaded("ordinary"))

    await store.set_lifecycle_state(sid, "archived", 1.0)

    row = (await tmp_db.fetch_all(
        "SELECT lifecycle_state FROM skills WHERE skill_id = ?", (sid,)))[0]
    assert row["lifecycle_state"] == "archived"


@pytest.mark.asyncio
async def test_a_pinned_skill_survives_retirement(tmp_db) -> None:
    """The protection that already existed — now reachable."""
    store = SkillIndexStore(tmp_db)
    sid = await store.upsert(_loaded("keeper"))
    await store.set_pinned(sid, True)

    await store.set_lifecycle_state(sid, "archived", 1.0)

    row = (await tmp_db.fetch_all(
        "SELECT lifecycle_state, pinned FROM skills WHERE skill_id = ?", (sid,)))[0]
    assert row["pinned"] == 1
    assert row["lifecycle_state"] == "active", (
        "the curator must not retire a skill the operator pinned"
    )


@pytest.mark.asyncio
async def test_unpinning_returns_the_skill_to_the_curator(tmp_db) -> None:
    """A protection you cannot release is a different bug from one you cannot
    engage, and shipping only the pin half would have created it."""
    store = SkillIndexStore(tmp_db)
    sid = await store.upsert(_loaded("temporary"))
    await store.set_pinned(sid, True)
    await store.set_pinned(sid, False)

    await store.set_lifecycle_state(sid, "archived", 1.0)

    row = (await tmp_db.fetch_all(
        "SELECT lifecycle_state FROM skills WHERE skill_id = ?", (sid,)))[0]
    assert row["lifecycle_state"] == "archived"


def test_the_command_offers_the_verb_its_own_help_text_promises() -> None:
    """`/skill dedupe` told the operator "a pinned member wins outright" while no
    verb could pin. The catalogue is the contract; this asserts it is honest."""
    from stackowl.commands.skill_command import _SKILL_META

    verbs = {sc.name for sc in _SKILL_META.subcommands}
    assert {"pin", "unpin"} <= verbs, (
        "dedupe's description promises pinning decides which member survives"
    )

    dedupe = next(sc for sc in _SKILL_META.subcommands if sc.name == "dedupe")
    assert "pinned" in dedupe.description, (
        "if this promise is ever removed, the verbs it justifies should be "
        "re-examined rather than left behind"
    )


@pytest.mark.asyncio
async def test_the_verb_pins_through_the_real_dispatch(tmp_db, tmp_path: Path) -> None:
    """Through `handle()`, not by calling the handler directly.

    The verb existing in the catalogue and the verb WORKING are two claims, and
    this programme has already paid for a command that reported success while
    changing nothing (`/quiet`, D15.6). This drives the same `elif` chain a typed
    `/skill pin` reaches.
    """
    from types import SimpleNamespace

    from stackowl.commands.skill_command import SkillCommand
    from stackowl.skills.loader import SkillLoader

    store = SkillIndexStore(tmp_db)
    sid = await store.upsert(_loaded("keepme"))
    cmd = SkillCommand(store=store, loader=SkillLoader(), skills_root=tmp_path)
    state = SimpleNamespace(session_key="test-session")

    out = await cmd.handle("pin keepme", state)

    assert "keepme" in str(out) and "pinned" in str(out).lower()
    row = (await tmp_db.fetch_all(
        "SELECT pinned FROM skills WHERE skill_id = ?", (sid,)))[0]
    assert row["pinned"] == 1, "the reply said pinned; the row must agree"

    await cmd.handle("unpin keepme", state)
    row = (await tmp_db.fetch_all(
        "SELECT pinned FROM skills WHERE skill_id = ?", (sid,)))[0]
    assert row["pinned"] == 0
