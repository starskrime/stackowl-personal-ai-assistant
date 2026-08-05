"""DEBT-42 — re-deriving a known lesson must REINFORCE, not mint a duplicate.

Measured on the live catalog 2026-08-05: 265 of 407 learned skills (65%) are
`-1`/`-2`/`-3` variants of an existing base name, and one base name
(`recover_tool_search_unachieved_effect`) exists TWENTY-ONE times.

The root cause is not the suffix. `_cluster_already_covered` dedupes on
parent_traces — on the EVIDENCE — so the same lesson learned from a new
incident carries new trace_ids, never matches, and mints a variant. This is the
ADR-19 open-loop failure occurring inside the improvement loop itself: the
writer never reads what it already wrote.
"""

from pathlib import Path

import pytest

from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore
from stackowl.skills.synthesizer import _base_name


def _loaded(name: str) -> LoadedSkill:
    return LoadedSkill(
        manifest=SkillManifest(name=name, description="d", source="learned"),
        path=Path("/tmp/x"), body="b", tools_registered=0, owls_registered=0, tool_names=(),
    )


class _Cluster:
    sequence = ("web_fetch", "shell")
    size = 3


class _Synth:
    """The reinforcement check, bound to a real store.

    Deliberately not a full SkillSynthesizer: constructing one needs a provider,
    an outcome store, a consent gate and a workspace root, none of which this
    behaviour touches. The method under test only reads _skills.
    """

    def __init__(self, store: SkillIndexStore) -> None:
        self._skills = store

    _reinforce_if_known = None  # bound below


from stackowl.skills.synthesizer import SkillSynthesizer  # noqa: E402

_Synth._reinforce_if_known = SkillSynthesizer._reinforce_if_known


# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("foo", "foo"),
        ("foo-1", "foo"),
        ("recover_tool_search_unachieved_effect-21", "recover_tool_search_unachieved_effect"),
        ("plain-name", "plain-name"),      # non-numeric suffix untouched
        ("foo-1-2", "foo-1"),              # only the trailing counter is stripped
    ],
)
def test_base_name_strips_only_the_numeric_counter(name, expected):
    assert _base_name(name) == expected


@pytest.mark.asyncio
async def test_a_known_lesson_is_reinforced_not_duplicated(tmp_db):
    store = SkillIndexStore(tmp_db)
    sid = await store.upsert(_loaded("recover_tool_search"))
    before = (await tmp_db.fetch_all(
        "SELECT n_executions FROM skills WHERE skill_id = ?", (sid,)))[0]["n_executions"]

    known = await _Synth(store)._reinforce_if_known("recover_tool_search", _Cluster())

    assert known is True, "must refuse to mint a second copy"
    after = (await tmp_db.fetch_all(
        "SELECT n_executions FROM skills WHERE skill_id = ?", (sid,)))[0]["n_executions"]
    assert after == before + 1, "re-deriving a lesson must STRENGTHEN the one we hold"


@pytest.mark.asyncio
async def test_an_existing_numbered_variant_still_counts_as_known(tmp_db):
    """Without this the 265 historical duplicates would each read as a distinct
    lesson and the sprawl would keep compounding from where it stands."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("shell_retry_loop_breaker-13"))

    assert await _Synth(store)._reinforce_if_known("shell_retry_loop_breaker", _Cluster())


@pytest.mark.asyncio
async def test_a_genuinely_new_lesson_is_still_minted(tmp_db):
    """The guard must not turn the synthesizer off."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("something_else"))

    assert not await _Synth(store)._reinforce_if_known("a_brand_new_lesson", _Cluster())


@pytest.mark.asyncio
async def test_reinforcement_revives_an_archived_skill(tmp_db):
    """A lesson a fresh cluster just re-derived has plainly not stopped being
    relevant — ADR-19's revival path, reached from the learning side."""
    store = SkillIndexStore(tmp_db)
    sid = await store.upsert(_loaded("came_back"))
    await store.set_lifecycle_state(sid, "archived", 1.0)

    await _Synth(store)._reinforce_if_known("came_back", _Cluster())

    rows = await tmp_db.fetch_all(
        "SELECT lifecycle_state FROM skills WHERE skill_id = ?", (sid,))
    assert rows[0]["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_a_broken_store_falls_through_to_minting(tmp_db):
    """B5 — the dedupe check must never cost us a synthesis run. On any error we
    behave exactly as before DEBT-42."""
    class _Boom:
        async def list_for_source(self, *_a, **_k):
            raise RuntimeError("store down")

    assert not await _Synth(_Boom())._reinforce_if_known("anything", _Cluster())
