from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore


def _loaded(name="alpha", tool_names=()):
    return LoadedSkill(
        manifest=SkillManifest(name=name, description="d", source="user"),
        path=Path("/tmp/x"), body="body", tools_registered=len(tool_names),
        owls_registered=0, tool_names=tuple(tool_names),
    )


# The two summary no-clobber tests that stood here went with the field itself
# (D09.3 slice 5, migration 0110). They guarded author-vs-generated precedence
# on a column that no longer exists; there is no behaviour left to protect.
# The tool_names no-clobber test below is unrelated and stays.


@pytest.mark.asyncio
async def test_upsert_refreshes_tool_names_from_disk(tmp_db: DbPool):
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded(tool_names=("t1",)))
    await store.upsert(_loaded(tool_names=("t1", "t2")))      # skill gained a tool
    sk = await store.get("user", "alpha")
    assert set(sk.tool_names) == {"t1", "t2"}
