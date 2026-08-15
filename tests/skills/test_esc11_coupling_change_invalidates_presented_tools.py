"""ESC-11 — a skill's coupled tools change, so presented arrays must be dropped.

THE GAP. `execute()` memoizes the tool array a model is shown, keyed on
`(session_key, owl, protocol, window, hydrated)`. The PINS are deliberately not
in that key: Law 1 wants the array byte-stable for the life of a conversation or
the cached prefix is void. The consequence was that installing a skill
mid-session never offered the tools that skill couples until the session rolled
over — "I gave my owl a skill and nothing happened".

WHY IT REUSES THE CAPABILITY HOOK (Bakir, 2026-08-15) rather than adding a second
invalidation path: the reasoning already written into
`presented_tools._on_capability_change` applies verbatim with "skill" substituted
for "capability", and this platform is self-extending, so an owl gaining a skill
at runtime is the intended path rather than an edge case.

THE NEGATIVE TEST IS THE LOAD-BEARING ONE. Every startup re-scan calls `upsert`
for every skill. Invalidating on all of them would drop every memoized array on
each scan — which is precisely the per-turn prefix churn ESC-12 is open to fix.
A fix that caused the bug next door would be worse than the gap it closed.

RELATIONSHIP TO D01.4, which invalidates on a skill change too and is NOT this:
that one clears the frozen PROMPT (`SessionPromptStore`) when the catalogue TEXT
changes on enable/disable. This one clears the presented TOOL ARRAY when the
coupling changes on upsert. Different cache, different trigger, one mechanism
each — see tests/skills/test_d014_skill_change_invalidates.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore

pytestmark = pytest.mark.asyncio


def _loaded(name: str, tools: tuple[str, ...]) -> LoadedSkill:
    return LoadedSkill(
        manifest=SkillManifest(
            name=name,
            description=f"{name} does something useful for the user",
            source="installed",
            summary=f"{name} summary",
        ),
        path=Path(f"/tmp/{name}"),
        body=f"body of {name}",
        tools_registered=len(tools),
        owls_registered=0,
        tool_names=tools,
    )


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every capability invalidation the store triggers."""
    from stackowl.infra import capabilities

    seen: list[str] = []
    real = capabilities.invalidate_cache
    monkeypatch.setattr(
        capabilities,
        "invalidate_cache",
        lambda name=None: (seen.append(str(name)), real(name))[1],
    )
    return seen


class TestItFiresOnARealChange:
    async def test_a_new_skill_with_coupled_tools_invalidates(
        self, tmp_db: DbPool, spy: list[str]
    ) -> None:
        await SkillIndexStore(tmp_db).upsert(_loaded("ops_helper", ("skill_only_tool",)))

        assert spy == ["skill:ops_helper"], spy

    async def test_changing_the_coupled_tools_invalidates(
        self, tmp_db: DbPool, spy: list[str]
    ) -> None:
        store = SkillIndexStore(tmp_db)
        await store.upsert(_loaded("ops_helper", ("tool_a",)))
        spy.clear()

        await store.upsert(_loaded("ops_helper", ("tool_a", "tool_b")))

        assert spy == ["skill:ops_helper"], spy

    async def test_REMOVING_a_coupled_tool_also_invalidates(
        self, tmp_db: DbPool, spy: list[str]
    ) -> None:
        """The direction that matters for least privilege: a tool that is no
        longer coupled must stop being pinned, not linger for the session."""
        store = SkillIndexStore(tmp_db)
        await store.upsert(_loaded("ops_helper", ("tool_a", "tool_b")))
        spy.clear()

        await store.upsert(_loaded("ops_helper", ("tool_a",)))

        assert spy == ["skill:ops_helper"], spy


class TestItStaysSilentOtherwise:
    async def test_a_NO_OP_RESCAN_does_not_invalidate(
        self, tmp_db: DbPool, spy: list[str]
    ) -> None:
        """The load-bearing negative. Startup re-scans upsert every skill; if that
        dropped every memoized array, this fix would cause exactly the per-turn
        prefix churn ESC-12 exists to remove."""
        store = SkillIndexStore(tmp_db)
        await store.upsert(_loaded("ops_helper", ("tool_a",)))
        spy.clear()

        await store.upsert(_loaded("ops_helper", ("tool_a",)))
        await store.upsert(_loaded("ops_helper", ("tool_a",)))

        assert spy == [], f"a re-scan that changed nothing invalidated anyway: {spy}"

    async def test_a_skill_with_no_tools_rescanned_is_silent(
        self, tmp_db: DbPool, spy: list[str]
    ) -> None:
        """Most skills couple nothing at all — they must be the quiet case."""
        store = SkillIndexStore(tmp_db)
        await store.upsert(_loaded("plain", ()))
        spy.clear()

        await store.upsert(_loaded("plain", ()))

        assert spy == []

    async def test_a_body_only_edit_does_not_invalidate(
        self, tmp_db: DbPool, spy: list[str]
    ) -> None:
        """Editing a skill's prose changes the catalogue, which is D01.4's
        business (it clears the frozen PROMPT). It does not change which tools
        are pinned, so the tool-array memo must not be dropped for it."""
        store = SkillIndexStore(tmp_db)
        await store.upsert(_loaded("ops_helper", ("tool_a",)))
        spy.clear()

        edited = _loaded("ops_helper", ("tool_a",))
        edited = LoadedSkill(
            manifest=edited.manifest,
            path=edited.path,
            body="a completely rewritten body",
            tools_registered=edited.tools_registered,
            owls_registered=edited.owls_registered,
            tool_names=edited.tool_names,
        )
        await store.upsert(edited)

        assert spy == []


class TestTheWriteSurvivesTheInvalidation:
    async def test_the_upsert_still_persists_its_row(self, tmp_db: DbPool) -> None:
        """Invalidation must never become a condition of the write succeeding."""
        store = SkillIndexStore(tmp_db)

        await store.upsert(_loaded("ops_helper", ("skill_only_tool",)))

        found = await store.get_many_by_name(("ops_helper",))
        assert found and found[0].tool_names == ("skill_only_tool",)

    async def test_a_raising_invalidation_does_not_lose_the_write(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cache that cannot be cleared is a stale cache, not a failed install.
        The user's skill must still be there."""
        from stackowl.infra import capabilities

        def _boom(name: str | None = None) -> None:
            raise RuntimeError("invalidation exploded")

        monkeypatch.setattr(capabilities, "invalidate_cache", _boom)

        await SkillIndexStore(tmp_db).upsert(_loaded("ops_helper", ("tool_a",)))

        found = await SkillIndexStore(tmp_db).get_many_by_name(("ops_helper",))
        assert found and found[0].tool_names == ("tool_a",)
