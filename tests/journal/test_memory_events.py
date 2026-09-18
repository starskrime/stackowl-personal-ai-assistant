"""``memory.written``/``memory.reflection_recorded`` are registered correctly
(Story 2.8), and ``record_md_memory_write`` is atomic with its own
transaction (AD-24), no-ops with no ``db_pool``, and redacts secrets through
a REAL emitter (NFR33) -- the same proof shape
``tests/journal/test_turn_events.py`` uses for Story 2.7's types.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.journal import AttentionClass, RecordKind, classify
from stackowl.journal.enums import ActorKind
from stackowl.journal.memory_events import (
    MemoryWrittenAttrs,
    ReflectionRecordedAttrs,
    record_md_memory_write,
)
from stackowl.journal.narrator import narrate_full
from stackowl.journal.registry import get_registry
from stackowl.paths import StackowlHome

pytestmark = pytest.mark.asyncio

# Synthetic canary only -- never a real credential (mirrors
# test_turn_events.py's own BEARER_CANARY, comfortably under the 64-char
# attrs bound).
BEARER_CANARY = "Bearer sk-canary1234567890abcdefghijklmno"


class TestTheTypesAreRegistered:
    def test_memory_written_is_memory_ambient(self) -> None:
        spec = get_registry().get("memory.written")
        assert spec.attrs_model is MemoryWrittenAttrs
        assert spec.record_kind is RecordKind.MEMORY
        assert spec.emitting_process == "memory.curated"
        assert classify("memory.written") == (AttentionClass.AMBIENT, None)

    def test_memory_reflection_recorded_is_memory_ambient(self) -> None:
        spec = get_registry().get("memory.reflection_recorded")
        assert spec.attrs_model is ReflectionRecordedAttrs
        assert spec.record_kind is RecordKind.MEMORY
        assert spec.emitting_process == "memory.reflection_writer_handler"
        assert classify("memory.reflection_recorded") == (AttentionClass.AMBIENT, None)


class TestNarration:
    def test_memory_written_names_the_target(self) -> None:
        spec = get_registry().get("memory.written")
        text = narrate_full(
            spec, MemoryWrittenAttrs(target="user", op="add", durability="permanent"), "x",
        )
        assert "user" in text

    def test_memory_reflection_recorded_names_the_owl(self) -> None:
        spec = get_registry().get("memory.reflection_recorded")
        text = narrate_full(
            spec,
            ReflectionRecordedAttrs(owl_name="scout", failure_class=None, quality_score=0.9),
            "x",
        )
        assert "scout" in text


class TestMemoryWrittenAttrsNeverCarriesFreeText:
    def test_an_undeclared_field_is_refused(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MemoryWrittenAttrs(
                target="user", op="add", durability="permanent",
                raw_text="the entry text must never fit here",  # type: ignore[call-arg]
            )

    def test_op_only_accepts_the_closed_vocabulary(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MemoryWrittenAttrs(target="user", op="delete", durability=None)  # type: ignore[arg-type]


class TestANoneDbPoolIsASilentNoOp:
    async def test_no_db_pool_writes_no_row_and_never_raises(self, tmp_path: Path) -> None:
        await record_md_memory_write(
            None, target="user", path=tmp_path / "USER.md", op="add",
            changed_index=0, durability="permanent",
            actor_kind=ActorKind.OWNER, actor_id="owner",
        )


class TestTheHelperCommitsExactlyOneRow:
    async def test_add_carries_the_anchor(
        self, tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
        path = StackowlHome.home() / "memory" / "USER.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[permanent] a fact\n", encoding="utf-8")

        await record_md_memory_write(
            tmp_db, target="user", path=path, op="add", changed_index=0,
            durability="permanent", actor_kind=ActorKind.OWNER, actor_id="owner",
        )

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'memory.written' AND target_id = ?",
            ("user",),
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["actor_kind"] == "owner"
        assert row["actor_id"] == "owner"
        assert row["target_kind"] == "owner"
        assert row["outcome"] == "ok"
        assert row["attention"] == "ambient"
        ref = json.loads(row["record_ref"])
        assert ref["kind"] == "md"
        assert ref["locator"]["path"] == "memory/USER.md"
        assert ref["locator"]["anchor"] == "entry-0"
        attrs = json.loads(row["attrs"])
        assert attrs["op"] == "add"
        assert attrs["durability"] == "permanent"

    async def test_remove_carries_no_anchor(
        self, tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
        path = StackowlHome.home() / "memory" / "scout.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

        await record_md_memory_write(
            tmp_db, target="scout", path=path, op="remove", changed_index=None,
            durability=None, actor_kind=ActorKind.OWL, actor_id="scout",
        )

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'memory.written' AND target_id = ?",
            ("scout",),
        )
        assert len(rows) == 1
        row = rows[0]
        # An owl's own notes target -- AD-3's one closed list doubling as
        # target_kind.
        assert row["target_kind"] == "owl"
        ref = json.loads(row["record_ref"])
        assert "anchor" not in ref["locator"]
        assert json.loads(row["attrs"])["op"] == "remove"

    async def test_no_trace_id_still_records(
        self, tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unlike Story 2.7's three types, a curated write records even with
        no trace in context -- a `/memory remember` from the CLI or a test
        construction has no turn behind it and must still be journaled."""
        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
        path = StackowlHome.home() / "memory" / "USER.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[permanent] a fact\n", encoding="utf-8")

        await record_md_memory_write(
            tmp_db, target="user", path=path, op="add", changed_index=0,
            durability="permanent", actor_kind=ActorKind.OWNER, actor_id="owner",
        )

        rows = await tmp_db.fetch_all(
            "SELECT trace_id FROM journal_events WHERE type = 'memory.written'",
        )
        assert len(rows) == 1
        assert rows[0]["trace_id"] is None


class TestTraceIdIsCarriedWhenPresent:
    async def test_trace_id_rides_the_ambient_context(
        self, tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
        path = StackowlHome.home() / "memory" / "USER.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[permanent] a fact\n", encoding="utf-8")

        token = TraceContext.start(trace_id="trace-memory-1")
        try:
            await record_md_memory_write(
                tmp_db, target="user", path=path, op="add", changed_index=0,
                durability="permanent", actor_kind=ActorKind.OWNER, actor_id="owner",
            )
        finally:
            TraceContext.reset(token)

        rows = await tmp_db.fetch_all(
            "SELECT trace_id FROM journal_events WHERE type = 'memory.written'",
        )
        assert rows[0]["trace_id"] == "trace-memory-1"


class TestTheHelperIsAtomicWithItsOwnTransaction:
    async def test_a_journal_record_failure_leaves_no_row(
        self, tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import memory_events as memory_events_module

        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
        path = StackowlHome.home() / "memory" / "USER.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[permanent] a fact\n", encoding="utf-8")

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(memory_events_module, "journal_record", _boom)

        # Never raises out (B5) -- the caller (`CuratedMemory._write`) must
        # never see this exception; the file write it follows has already
        # succeeded.
        await record_md_memory_write(
            tmp_db, target="user", path=path, op="add", changed_index=0,
            durability="permanent", actor_kind=ActorKind.OWNER, actor_id="owner",
        )

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'memory.written'",
        )
        assert rows == []


class TestARealJournalRecordFailureDegradesHealth:
    """AD-24's explicit clause: 'a failed record marks the journal health
    contributor degraded.' Unlike the atomicity test above (which monkeypatches
    the whole ``journal_record`` function, bypassing recorder.py's own degrade
    logic entirely), this forces a GENUINE internal ``journal.record()``
    failure -- an unregistered event type, the same failure shape
    ``recorder.py``'s own ``get_registry().get(event.type)`` call raises on --
    so recorder.py's real ``except`` block, real ``note_failure`` call, are
    what is under test here."""

    async def test_an_unregistered_type_degrades_the_real_contributor(
        self, tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal.health import JournalHealthContributor
        from stackowl.journal.registry import get_registry

        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
        path = StackowlHome.home() / "memory" / "USER.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[permanent] a fact\n", encoding="utf-8")

        registry = get_registry()
        saved_spec = registry._specs.pop("memory.written")  # noqa: SLF001 -- force recorder.py's REAL unregistered-type failure path
        try:
            await record_md_memory_write(
                tmp_db, target="user", path=path, op="add", changed_index=0,
                durability="permanent", actor_kind=ActorKind.OWNER, actor_id="owner",
            )
        finally:
            registry._specs["memory.written"] = saved_spec  # noqa: SLF001

        status = await JournalHealthContributor().health_check()
        assert status.status == "degraded"


class TestCanarySecretsAreRedactedThroughARealEmitter:
    """NFR33: driven through the REAL recording helper -- no canary ever
    appears unredacted in ``journal_events.attrs``."""

    async def test_memory_written(
        self, tmp_db: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
        path = StackowlHome.home() / "memory" / "USER.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[permanent] a fact\n", encoding="utf-8")

        await record_md_memory_write(
            tmp_db, target=BEARER_CANARY, path=path, op="add", changed_index=0,
            durability="permanent", actor_kind=ActorKind.OWNER, actor_id="owner",
        )

        rows = await tmp_db.fetch_all(
            "SELECT attrs FROM journal_events WHERE type = 'memory.written'",
        )
        assert len(rows) == 1
        stored = rows[0]["attrs"]
        assert BEARER_CANARY not in stored
        assert json.loads(stored)["_redacted"] is True
