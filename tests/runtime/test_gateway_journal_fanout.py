"""``GatewayLink`` journal fan-out (Spec 2.5): a pushed ``JournalEventFrame``
narrates and re-emits ``pipeline_step_changed``; the ``last_delivered_cursor``
watermark is the one de-dup authority; a ``HelloFrame`` (re)connect catches up
from the gateway's own last delivered cursor before anything else resumes.

Extends ``test_split_restart.py``'s ``_FakeConn``/Hello-fixture shape and adds
a real tmp-DB ``DbPool`` as the ``RowFetcher`` for the catch-up path
(``RowFetcher`` matches ``DbPool.fetch_all``'s shape structurally -- no fake
needed there).
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.ipc.frames import HelloFrame, JournalEventFrame
from stackowl.journal import ActorKind, JournalEvent, Outcome, RecordRef, record
from stackowl.journal.fanout import current_max_cursor, read_since
from stackowl.journal.narrator import reset_name_resolvers_for_tests
from stackowl.journal.task_events import TaskEnqueuedAttrs
from stackowl.runtime.gateway_link import _NOMINAL_TOTAL_STEPS, GatewayLink

pytestmark = pytest.mark.asyncio

#: Spec 2.4 precedent (test_split_restart.py) -- one fixed per-file secret for
#: every GatewayLink construction AND every HelloFrame representing a real core.
_LINK_SECRET = "test-link-secret"

#: With no ``NameResolver`` registered for ``RecordKind.TASK`` (this file's
#: autouse fixture below guarantees that), ``narrate()`` always falls back to
#: the generic tombstone -- deterministic regardless of ``target_id``.
_EXPECTED_STEP_NAME = "Task a retired task was queued."


@pytest.fixture(autouse=True)
def _reset_name_resolvers():  # noqa: ANN201 — pytest fixture
    """Mirrors ``tests/journal/conftest.py``'s own reset: this file never
    registers a resolver, so narration is deterministic (the tombstone) --
    but the resolver registry is process-global state another test file could
    otherwise leak into this one."""
    reset_name_resolvers_for_tests()
    yield
    reset_name_resolvers_for_tests()


class _FakeConn:
    """Records frames sent; never yields inbound (mirrors test_split_restart.py)."""

    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, frame: object) -> None:
        self.sent.append(frame)


class _FakeAdapter:
    channel_name = "cli"

    async def send(self, reader) -> None:  # noqa: ANN001
        pass

    async def send_text(self, text: str) -> None:
        pass


class _FakeEventBus:
    """The ``_EventSink`` slice ``GatewayLink`` uses -- records every emit."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, object]]] = []

    def emit(self, event: str, payload: object) -> None:
        self.emitted.append((event, payload))  # type: ignore[arg-type]


def _hello(pid: int = 1) -> HelloFrame:
    return HelloFrame(
        sender_pid=pid, highest_migration=1, registry_digest="x", link_secret=_LINK_SECRET,
    )


def _journal_frame(*, cursor: int, target_id: str = "t1") -> JournalEventFrame:
    return JournalEventFrame(
        cursor=cursor,
        event_id=f"ev-{cursor}",
        event_type="task.enqueued",
        schema_version=1,
        occurred_at="2026-09-17T00:00:00+00:00",
        actor_kind="autonomous",
        actor_id="owner",
        target_kind="owner",
        target_id=target_id,
        outcome="pending",
        record_ref={"kind": "sqlite", "locator": {"table": "tasks", "task_id": target_id}},
        attrs={"trigger_kind": "chat", "depends_on_count": 0, "max_attempts": 3},
        trace_id="t1",
        duration_ms=None,
    )


async def _record_task_enqueued(db: DbPool, target_id: str) -> None:
    async with db.transaction() as conn:
        await record(conn, JournalEvent(
            type="task.enqueued",
            schema_version=1,
            actor_kind=ActorKind.AUTONOMOUS,
            actor_id="owner",
            target_kind=ActorKind.OWNER,
            target_id=target_id,
            outcome=Outcome.PENDING,
            record_ref=RecordRef(
                kind="sqlite", locator={"table": "tasks", "task_id": target_id},
            ),
            attrs=TaskEnqueuedAttrs(trigger_kind="chat", depends_on_count=0, max_attempts=3),
        ))


class TestDeliverJournalEvent:
    async def test_narrates_and_emits_pipeline_step_changed(self) -> None:
        bus = _FakeEventBus()
        link = GatewayLink({"cli": _FakeAdapter()}, event_bus=bus, link_secret=_LINK_SECRET)

        await link._route(_journal_frame(cursor=1))

        assert len(bus.emitted) == 1
        event, payload = bus.emitted[0]
        assert event == "pipeline_step_changed"
        assert payload["step_name"] == _EXPECTED_STEP_NAME
        # `step_index`/`total_steps` are a small local, wrapped UI counter —
        # never the journal `cursor` (a global, ever-growing counter would
        # make `PipelineStrip`'s bounded "train" always render fully filled;
        # see `GatewayLink.__init__`'s `_progress_step_index` comment).
        assert payload["step_index"] == 1
        assert payload["total_steps"] == _NOMINAL_TOTAL_STEPS

    async def test_a_stale_or_duplicate_cursor_is_dropped(self) -> None:
        bus = _FakeEventBus()
        link = GatewayLink({"cli": _FakeAdapter()}, event_bus=bus, link_secret=_LINK_SECRET)

        await link._route(_journal_frame(cursor=5))
        await link._route(_journal_frame(cursor=5))  # exact duplicate
        await link._route(_journal_frame(cursor=3))  # stale — below the watermark

        assert len(bus.emitted) == 1

    async def test_watermark_advances_on_each_higher_cursor(self) -> None:
        bus = _FakeEventBus()
        link = GatewayLink({"cli": _FakeAdapter()}, event_bus=bus, link_secret=_LINK_SECRET)

        await link._route(_journal_frame(cursor=1))
        await link._route(_journal_frame(cursor=2))

        assert len(bus.emitted) == 2
        assert link._last_delivered_cursor == 2

    async def test_no_event_bus_is_a_safe_no_op(self) -> None:
        """No EventBus injected (e.g. a CLI-only config) — the row is still
        narrated (advancing the watermark) but there is nothing to emit onto."""
        link = GatewayLink({"cli": _FakeAdapter()}, link_secret=_LINK_SECRET)

        await link._route(_journal_frame(cursor=1))

        assert link._last_delivered_cursor == 1

    async def test_a_row_that_fails_narration_still_advances_the_watermark(self) -> None:
        """The poisoned-row guard in ``_deliver_journal_row`` (AD-9: never
        block fan-out on a hole) — an unregistered ``event_type`` makes
        ``get_registry().get(...)`` raise, caught by the ``except Exception``
        branch. The watermark must still advance to that row's cursor (so a
        persistently-poisoned row can never wedge every row after it), and
        nothing may be emitted onto the bus for it."""
        bus = _FakeEventBus()
        link = GatewayLink({"cli": _FakeAdapter()}, event_bus=bus, link_secret=_LINK_SECRET)

        poisoned = _journal_frame(cursor=9).model_copy(
            update={"event_type": "no.such.registered.type"}
        )
        await link._route(poisoned)

        assert bus.emitted == []
        assert link._last_delivered_cursor == 9

        # And it does not wedge a later, valid row either.
        await link._route(_journal_frame(cursor=10))
        assert len(bus.emitted) == 1
        assert link._last_delivered_cursor == 10


class TestHelloCatchUp:
    async def test_a_hello_reconnect_catches_up_rows_committed_during_the_gap(
        self, tmp_db: DbPool
    ) -> None:
        bus = _FakeEventBus()
        link = GatewayLink(
            {"cli": _FakeAdapter()}, event_bus=bus, link_secret=_LINK_SECRET,
            journal_fetcher=tmp_db,
        )
        conn = _FakeConn()

        # Two rows committed on the gateway's OWN DbPool while the link was
        # down — the gateway never got a live push for them.
        await _record_task_enqueued(tmp_db, "gap-1")
        await _record_task_enqueued(tmp_db, "gap-2")
        expected_max_cursor = (await read_since(tmp_db, 0))[-1].cursor

        link.set_connection(conn, local_hello=_hello())
        await link._route(_hello())

        # Both gap rows were narrated + emitted, IN CURSOR ORDER, as part of
        # the Hello branch itself (before anything else in this test could
        # have delivered them).
        assert [p["step_name"] for _, p in bus.emitted] == [
            _EXPECTED_STEP_NAME, _EXPECTED_STEP_NAME,
        ]
        assert [p["step_index"] for _, p in bus.emitted] == sorted(
            p["step_index"] for _, p in bus.emitted
        )
        # `_last_delivered_cursor` tracks the journal `cursor`, NOT the small
        # `step_index` UI counter (see `test_narrates_and_emits_pipeline_step_changed`).
        assert link._last_delivered_cursor == expected_max_cursor

    async def test_catch_up_never_redelivers_a_row_already_pushed_live(
        self, tmp_db: DbPool
    ) -> None:
        """A live push racing a reconnect catch-up is absorbed by the SAME
        watermark (Boundaries & Constraints) — no double emission."""
        bus = _FakeEventBus()
        link = GatewayLink(
            {"cli": _FakeAdapter()}, event_bus=bus, link_secret=_LINK_SECRET,
            journal_fetcher=tmp_db,
        )
        conn = _FakeConn()

        await _record_task_enqueued(tmp_db, "live-1")
        cursor = (await read_since(tmp_db, 0))[0].cursor

        # Delivered once, live, BEFORE any Hello/catch-up ever runs.
        await link._route(_journal_frame(cursor=cursor, target_id="live-1"))
        assert len(bus.emitted) == 1

        # A reconnect's catch-up now sees the SAME already-committed row.
        link.set_connection(conn, local_hello=_hello())
        await link._route(_hello())

        assert len(bus.emitted) == 1  # not re-delivered

    async def test_catch_up_runs_before_pending_turns_are_flushed(
        self, tmp_db: DbPool
    ) -> None:
        """AC3: catch-up happens BEFORE live delivery/flush resumes. Proven
        via ordering: a pending IngressFrame send and the catch-up emit both
        happen inside the same ``_route(HelloFrame)`` call, and the catch-up
        emit must be visible by the time that call returns — this test
        asserts both landed, which only happens if catch-up did not hang or
        get skipped ahead of the flush."""
        from stackowl.gateway.scanner import IngressMessage
        from stackowl.ipc.frames import IngressFrame

        bus = _FakeEventBus()
        link = GatewayLink(
            {"cli": _FakeAdapter()}, event_bus=bus, link_secret=_LINK_SECRET,
            journal_fetcher=tmp_db,
        )
        conn = _FakeConn()
        await _record_task_enqueued(tmp_db, "ordered-1")

        await link.submit(
            IngressMessage(text="hi", session_key="s1", channel="cli", trace_id="t-x")
        )
        link.set_connection(conn, local_hello=_hello())
        await link._route(_hello())

        assert len(bus.emitted) == 1  # the catch-up row was delivered
        assert any(isinstance(f, IngressFrame) for f in conn.sent)  # and flush ran

    async def test_catch_up_drains_more_than_one_page_of_gap_rows(
        self, tmp_db: DbPool
    ) -> None:
        """AC3 — a gap wider than one ``read_since`` page (200 rows) must not
        be silently under-delivered: ``_catch_up_journal`` must loop across
        pages, not just read the first one."""
        bus = _FakeEventBus()
        link = GatewayLink(
            {"cli": _FakeAdapter()}, event_bus=bus, link_secret=_LINK_SECRET,
            journal_fetcher=tmp_db,
        )
        conn = _FakeConn()

        total_rows = 250  # > the 200-row page size on both sides of the pipe
        async with tmp_db.transaction() as txn_conn:
            for i in range(total_rows):
                await record(txn_conn, JournalEvent(
                    type="task.enqueued",
                    schema_version=1,
                    actor_kind=ActorKind.AUTONOMOUS,
                    actor_id="owner",
                    target_kind=ActorKind.OWNER,
                    target_id=f"gap-{i}",
                    outcome=Outcome.PENDING,
                    record_ref=RecordRef(
                        kind="sqlite", locator={"table": "tasks", "task_id": f"gap-{i}"},
                    ),
                    attrs=TaskEnqueuedAttrs(
                        trigger_kind="chat", depends_on_count=0, max_attempts=3,
                    ),
                ))
        true_max_cursor = await current_max_cursor(tmp_db)

        link.set_connection(conn, local_hello=_hello())
        await link._route(_hello())

        assert len(bus.emitted) == total_rows
        assert link._last_delivered_cursor == true_max_cursor

    async def test_no_fetcher_injected_is_a_safe_no_op_catch_up(self) -> None:
        """No DbPool injected (most unit tests, or a link with no catch-up
        capability wired) — a Hello still succeeds; it just skips catch-up."""
        bus = _FakeEventBus()
        link = GatewayLink({"cli": _FakeAdapter()}, event_bus=bus, link_secret=_LINK_SECRET)
        conn = _FakeConn()

        link.set_connection(conn, local_hello=_hello())
        await link._route(_hello())

        assert bus.emitted == []
