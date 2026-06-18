import pytest

from pathlib import Path
from stackowl.db.pool import DbPool
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore


def _loaded(name="alpha"):
    return LoadedSkill(
        manifest=SkillManifest(name=name, description="d", source="user"),
        path=Path("/tmp/x"), body="body",
        tools_registered=0, owls_registered=0, tool_names=(),
    )


@pytest.mark.asyncio
async def test_get_exposes_summary_and_tool_names_defaults(tmp_db: DbPool):
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded())
    sk = await store.get("user", "alpha")
    assert sk is not None
    assert sk.summary is None
    assert sk.summary_source is None
    assert sk.summary_body_hash is None
    assert sk.tool_names == ()
