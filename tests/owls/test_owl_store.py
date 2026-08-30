"""OwlStore — one SQLite home for an owl (migration 0118).

BAKIR, 2026-08-16: "Nothing should live in memory, everything in md or sqlite. No
data duplication md file or sqlite."

MEASURED: one owl lived in four places — stackowl.yaml (12 authoritative
manifests), owl_dna (12), owl_dna_authored (17, so 5 orphans), owl_profiles (0
rows, nothing writes it) — plus the in-memory registry. The rename bug the same
day came out of exactly that split.

These tests pin the two properties that make the new home trustworthy: the derived
index columns can never disagree with the manifest they index, and the one-time
seed cannot resurrect an owl the user has retired.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.store import OwlStore
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio


def _owl(name: str = "secretary", **over: object) -> OwlAgentManifest:
    base: dict = dict(
        name=name,
        role="primary-assistant",
        system_prompt="You are the Secretary.",
        model_tier="powerful",
    )
    base.update(over)
    return OwlAgentManifest(**base)


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))

    db = DbPool(db_path=tmp_path / "t.db")
    await db.open()
    seed_schema(tmp_path / "t.db")
    yield OwlStore(db)
    await db.close()


class TestItRoundTrips:
    async def test_an_owl_survives_a_write_and_read(self, store: OwlStore) -> None:
        await store.upsert(_owl(display_name="Friday"))

        got = await store.list_all()

        assert [m.name for m in got] == ["secretary"]
        assert got[0].display_name == "Friday"
        assert got[0].system_prompt == "You are the Secretary."

    async def test_a_nested_field_survives(self, store: OwlStore) -> None:
        """The reason the manifest is stored as ONE json document rather than a
        column per field: nested models must round-trip without a hand-written
        column for each, which would drift the moment the model changes."""
        await store.upsert(_owl(tools=["web_search", "memory"]))

        got = (await store.list_all())[0]

        assert list(got.tools) == ["web_search", "memory"]

    async def test_upsert_replaces_rather_than_duplicates(self, store: OwlStore) -> None:
        await store.upsert(_owl(display_name="Mary"))
        await store.upsert(_owl(display_name="Friday"))

        got = await store.list_all()

        assert len(got) == 1, "the same owl was stored twice"
        assert got[0].display_name == "Friday"


class TestTheIndexCannotDisagreeWithTheDocument:
    async def test_the_derived_columns_track_the_manifest(self, store: OwlStore) -> None:
        """display_name/role/lifecycle/origin are an INDEX over manifest_json. If a
        rename updated the document but not the column, listing would show the old
        name — the same class of bug as the rename that started all this."""
        await store.upsert(_owl(display_name="Mary"))
        await store.upsert(_owl(display_name="Friday"))

        rows = await store._db.fetch_all(  # noqa: SLF001
            "SELECT display_name, manifest_json FROM owls WHERE name='secretary'", ()
        )

        assert rows[0]["display_name"] == "Friday"
        assert '"display_name":"Friday"' in rows[0]["manifest_json"].replace(" ", "")


class TestTheSeedIsSafe:
    async def test_it_populates_an_empty_table(self, store: OwlStore) -> None:
        n = await store.seed_from([_owl("secretary"), _owl("scout")])

        assert n == 2
        assert {m.name for m in await store.list_all()} == {"secretary", "scout"}

    async def test_it_never_runs_twice(self, store: OwlStore) -> None:
        """Idempotent by the emptiness check. A second seed must be a no-op even
        when it is offered different owls."""
        await store.seed_from([_owl("secretary")])

        n = await store.seed_from([_owl("secretary"), _owl("scout")])

        assert n == 0
        assert {m.name for m in await store.list_all()} == {"secretary"}

    async def test_it_cannot_resurrect_a_RETIRED_owl(self, store: OwlStore) -> None:
        """The property that matters most. Once the table is live, the YAML is a
        stale artefact; re-seeding from it would bring back an owl the user
        deliberately retired."""
        await store.seed_from([_owl("secretary"), _owl("scout")])
        await store.delete("scout")

        await store.seed_from([_owl("secretary"), _owl("scout")])

        assert {m.name for m in await store.list_all()} == {"secretary"}


class TestItDegradesRatherThanCrashingTheBoot:
    async def test_one_corrupt_owl_does_not_cost_the_others(
        self, store: OwlStore
    ) -> None:
        """A registry that refuses to load because one row is bad is worse than a
        registry that loads eleven of twelve and says so loudly."""
        await store.upsert(_owl("secretary"))
        await store._db.execute(  # noqa: SLF001
            "INSERT INTO owls (name, manifest_json) VALUES ('broken', '{not json')"
        )

        got = await store.list_all()

        assert [m.name for m in got] == ["secretary"]

    async def test_delete_reports_whether_anything_was_removed(
        self, store: OwlStore
    ) -> None:
        await store.upsert(_owl("secretary"))

        assert await store.delete("secretary") is True
        assert await store.delete("secretary") is False
