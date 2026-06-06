from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore


def _loaded(name="alpha", summary=None, tool_names=()):
    return LoadedSkill(
        manifest=SkillManifest(name=name, description="d", source="user", summary=summary),
        path=Path("/tmp/x"), body="body", tools_registered=len(tool_names),
        owls_registered=0, tool_names=tuple(tool_names),
    )


@pytest.mark.asyncio
async def test_reboot_upsert_does_not_clobber_generated_summary(tmp_db: DbPool):
    store = SkillIndexStore(tmp_db)
    sid = await store.upsert(_loaded())                       # no author summary
    await store.set_summary(sid, "GENERATED PLAYBOOK", "generated", "hash123")
    await store.upsert(_loaded())                             # reboot re-scan, still no author summary
    sk = await store.get("user", "alpha")
    assert sk.summary == "GENERATED PLAYBOOK"                 # survived reboot
    assert sk.summary_source == "generated"


@pytest.mark.asyncio
async def test_author_summary_persists_and_wins(tmp_db: DbPool):
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded(summary="AUTHORED"))
    sk = await store.get("user", "alpha")
    assert sk.summary == "AUTHORED"
    assert sk.summary_source == "author"


@pytest.mark.asyncio
async def test_upsert_refreshes_tool_names_from_disk(tmp_db: DbPool):
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded(tool_names=("t1",)))
    await store.upsert(_loaded(tool_names=("t1", "t2")))      # skill gained a tool
    sk = await store.get("user", "alpha")
    assert set(sk.tool_names) == {"t1", "t2"}
