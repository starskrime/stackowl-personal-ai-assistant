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
async def test_get_exposes_tool_names_defaults(tmp_db: DbPool):
    """The summary half of this test went with the field (D09.3 slice 5,
    migration 0110). The tool_names half is unrelated and still guards that a
    skill with no declared tools reads back as an empty tuple, not None."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded())
    sk = await store.get("user", "alpha")
    assert sk is not None
    assert sk.tool_names == ()
    assert not hasattr(sk, "summary"), "the field is gone, not merely unset"
