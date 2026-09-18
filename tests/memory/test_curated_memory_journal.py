"""Story 2.8 -- ``CuratedMemory.add``/``replace``/``remove`` each journal a
``memory.written`` event immediately after their file write (AD-24), via a
real `set_db_pool`-wired ``DbPool``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.journal.enums import ActorKind
from stackowl.memory import curated as curated_module
from stackowl.memory.curated import USER_TARGET, CuratedMemory

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CuratedMemory:
    # STACKOWL_HOME must point at the SAME tree `root` resolves under, or
    # `record_md_memory_write`'s `path.relative_to(StackowlHome.home())`
    # falls into its defensive ValueError branch and journals an absolute
    # path instead of the intended StackowlHome-relative locator -- silently,
    # since nothing else in this file would notice. `tests/conftest.py`'s
    # autouse `_isolate_stackowl_home` already points STACKOWL_HOME at a
    # DIFFERENT tmp_path_factory directory, so this must be set explicitly
    # (mirrors `tests/journal/test_memory_events.py`'s own pattern).
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    return CuratedMemory(root=tmp_path / "memory")


@pytest.fixture(autouse=True)
def _clear_db_pool() -> None:
    """`_DB_POOL` is a module global (mirrors `shared_memory()`'s own) --
    reset it around every test so one test's `set_db_pool` never leaks into
    a later, unrelated test."""
    curated_module._DB_POOL = None
    yield
    curated_module._DB_POOL = None


async def _rows(tmp_db: DbPool, target: str) -> list[dict]:
    return await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE type = 'memory.written' AND target_id = ? "
        "ORDER BY event_id",
        (target,),
    )


class TestNoDbPoolWiredIsANoOp:
    async def test_add_still_writes_the_file_with_nothing_journaled(
        self, mem: CuratedMemory,
    ) -> None:
        result = await mem.add(USER_TARGET, "A fact with no db_pool wired.", "permanent")
        assert result.ok is True
        assert mem.entries(USER_TARGET)


class TestAddJournalsWithTheLastIndexAsAnchor:
    async def test_first_entry_is_index_0(self, mem: CuratedMemory, tmp_db: DbPool) -> None:
        curated_module.set_db_pool(tmp_db)
        result = await mem.add(USER_TARGET, "Bakir prefers root-cause fixes.", "permanent")
        assert result.ok is True

        rows = await _rows(tmp_db, USER_TARGET)
        assert len(rows) == 1
        row = rows[0]
        assert row["outcome"] == "ok"
        attrs = json.loads(row["attrs"])
        assert attrs["op"] == "add"
        assert attrs["durability"] == "permanent"
        ref = json.loads(row["record_ref"])
        assert ref["kind"] == "md"
        assert ref["locator"]["anchor"] == "entry-0"
        # StackowlHome-relative, not absolute -- proves STACKOWL_HOME and
        # `root` actually agree (the ValueError fallback would leave an
        # absolute path here instead).
        assert ref["locator"]["path"] == "memory/USER.md"
        assert not Path(ref["locator"]["path"]).is_absolute()

    async def test_second_entry_is_index_1(self, mem: CuratedMemory, tmp_db: DbPool) -> None:
        curated_module.set_db_pool(tmp_db)
        await mem.add(USER_TARGET, "First fact.", "permanent")
        await mem.add(USER_TARGET, "Second fact.", "permanent")

        rows = await _rows(tmp_db, USER_TARGET)
        assert len(rows) == 2
        ref = json.loads(rows[1]["record_ref"])
        assert ref["locator"]["anchor"] == "entry-1"

    async def test_actor_kind_and_id_are_carried_through(
        self, mem: CuratedMemory, tmp_db: DbPool,
    ) -> None:
        curated_module.set_db_pool(tmp_db)
        await mem.add(
            USER_TARGET, "A fact.", "permanent",
            actor_kind=ActorKind.OWL, actor_id="scout",
        )
        rows = await _rows(tmp_db, USER_TARGET)
        assert rows[0]["actor_kind"] == "owl"
        assert rows[0]["actor_id"] == "scout"


class TestReplaceJournalsTheReplacedEntrysPreservedIndex:
    async def test_replacing_the_second_of_three_keeps_index_1(
        self, mem: CuratedMemory, tmp_db: DbPool,
    ) -> None:
        curated_module.set_db_pool(tmp_db)
        await mem.add(USER_TARGET, "First.", "permanent")
        await mem.add(USER_TARGET, "Second, the one to replace.", "permanent")
        await mem.add(USER_TARGET, "Third.", "permanent")

        result = await mem.replace(
            USER_TARGET, "the one to replace", "Second, replaced.", "permanent",
        )
        assert result.ok is True

        rows = await _rows(tmp_db, USER_TARGET)
        # 3 adds + 1 replace = 4 memory.written rows total.
        assert len(rows) == 4
        replace_row = rows[-1]
        attrs = json.loads(replace_row["attrs"])
        assert attrs["op"] == "replace"
        ref = json.loads(replace_row["record_ref"])
        assert ref["locator"]["anchor"] == "entry-1"


class TestRemoveJournalsWithNoAnchor:
    async def test_remove_carries_path_only(
        self, mem: CuratedMemory, tmp_db: DbPool,
    ) -> None:
        curated_module.set_db_pool(tmp_db)
        await mem.add(USER_TARGET, "A temporary fact.", "until_changed")

        result = await mem.remove(USER_TARGET, "temporary fact")
        assert result.ok is True

        rows = await _rows(tmp_db, USER_TARGET)
        remove_row = rows[-1]
        attrs = json.loads(remove_row["attrs"])
        assert attrs["op"] == "remove"
        ref = json.loads(remove_row["record_ref"])
        assert "anchor" not in ref["locator"]


class TestAFailedJournalWriteNeverCostsTheFileWrite:
    async def test_add_still_succeeds_when_journaling_raises(
        self, mem: CuratedMemory, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import memory_events as memory_events_module

        curated_module.set_db_pool(tmp_db)

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal failure")

        # Patches the INNER `journal.record()` call, not the whole helper --
        # `record_md_memory_write` itself is the thing under test here (B5:
        # its OWN swallow must be what protects `_write`, not a test that
        # bypasses it entirely).
        monkeypatch.setattr(memory_events_module, "journal_record", _boom)

        result = await mem.add(USER_TARGET, "This must still be saved.", "permanent")

        assert result.ok is True
        assert any(e.text == "This must still be saved." for e in mem.entries(USER_TARGET))
