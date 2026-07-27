"""D01.7 slice 3b part 5b — what the conversation boundary does with memory.

Real DbPool, real migrations, real SqliteMemoryBridge, real DurableTaskStore. The
AI provider is the only thing faked, and the miner is faked because it is itself
an LLM-driven component whose own behaviour is tested elsewhere — the question
here is what the HANDLER does with it.

THE TEST THAT MATTERS MOST is
``test_the_miner_is_scoped_to_the_person_not_the_lane``. ConversationMiner's
parameter is named ``session_key`` but is matched against ``staged_facts.source_ref``,
which ``turn_persist`` fills with the OWNER scope. Passing the owl-prefixed lane
would mine a source_ref that has no rows — silently, successfully, and for ever.
"""

from __future__ import annotations

import datetime
import json
import uuid

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.rollover_summary_handler import RolloverSummaryHandler
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio

LANE = "owl:Brain:telegram:dm:123"
ENDED = "20260726_040000_aaaaaaaa"
IDENTITY = "bakir"


class FakeMiner:
    """Records what it was asked to mine, which is the point of the test."""

    def __init__(self, staged: int = 3) -> None:
        self.calls: list[str] = []
        self._staged = staged

    async def mine_session(self, session_key: str) -> int:
        self.calls.append(session_key)
        return self._staged


class FakeProvider:
    """Only the AI provider is faked."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    async def complete(self, messages, model="", **kwargs):  # noqa: ANN001, ANN003
        self.prompts.append("\n".join(m.content for m in messages))

        class _R:
            content = self.content

        return _R()


class FakeRegistry:
    def __init__(self, provider: object, *, raises: bool = False) -> None:
        self._provider = provider
        self._raises = raises
        self.tiers: list[str] = []

    def get_with_cascade(self, tier: str):  # noqa: ANN201
        self.tiers.append(tier)
        if self._raises:
            raise RuntimeError("no provider available")
        return self._provider, "test-model"


def _notable(summary: str = "Agreed to split part 5 in two.") -> str:
    return json.dumps({"notable": True, "summary": summary})


_NOT_NOTABLE = json.dumps({"notable": False, "summary": ""})


def _job(**overrides: object) -> Job:
    params: dict[str, object] = {
        "session_key": LANE,
        "ended_session_id": ENDED,
        "identity_key": IDENTITY,
        "owl_name": "Brain",
        "reason": "daily",
        "run_once": True,
    }
    params.update(overrides)
    return Job(
        job_id=f"rollover_summary-{uuid.uuid4().hex[:8]}",
        handler_name="rollover_summary",
        schedule="manual",
        idempotency_key=f"rollover:{LANE}:{ENDED}:{uuid.uuid4().hex[:6]}",
        last_run_at=None,
        next_run_at="",
        status="pending",
        params=params,
    )


async def _write_transcript(db: DbPool, *, session_id: str = ENDED,
                            turns: int = 2) -> None:
    """A transcript for one incarnation, via the same tables TranscriptStore writes."""
    stamp = datetime.datetime.now(datetime.UTC)
    await db.execute(
        "INSERT INTO conversations (id, session_key, owl_name, started_at, message_count)"
        " VALUES (?, ?, ?, ?, ?)",
        (session_id, LANE, "Brain", stamp.isoformat(), turns * 2),
    )
    for i in range(turns):
        for role, text in (("user", f"question {i}"), ("assistant", f"answer {i}")):
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, content, model,"
                " created_at, trace_id) VALUES (?, ?, ?, ?, NULL, ?, '')",
                (str(uuid.uuid4()), session_id, role, text,
                 (stamp + datetime.timedelta(seconds=i)).isoformat()),
            )


def _handler(db: DbPool, miner: object, registry: object) -> RolloverSummaryHandler:
    return RolloverSummaryHandler(
        db=db, miner=miner, bridge=SqliteMemoryBridge(db), provider_registry=registry,
    )


# --------------------------------------------------------------------- the gate


async def test_a_lane_that_said_nothing_costs_nothing(tmp_db: DbPool) -> None:
    """No transcript for the ended incarnation → no paid call at all.

    This is the whole cost control after Q14's structural gate was withdrawn: the
    model decides notability, but only for a conversation that actually happened.
    """
    provider = FakeProvider(_notable())
    registry = FakeRegistry(provider)
    miner = FakeMiner()

    result = await _handler(tmp_db, miner, registry).execute(_job())

    assert result.success is True
    assert provider.prompts == [], "no transcript must mean no provider call"
    assert miner.calls == [], "and nothing to mine either"


# ------------------------------------------------------- the scoping invariant


async def test_the_miner_is_scoped_to_the_person_not_the_lane(tmp_db: DbPool) -> None:
    """mine_session's argument is a source_ref (the OWNER), not a session key.

    turn_persist files conversation records under owner_scope_key. Passing the
    owl-prefixed lane here would mine a source_ref with no rows — succeeding,
    reporting 0, and never learning anything.
    """
    await _write_transcript(tmp_db)
    miner = FakeMiner()

    await _handler(tmp_db, miner, FakeRegistry(FakeProvider(_notable()))).execute(_job())

    assert miner.calls == [IDENTITY], (
        f"must mine the identity, not the lane; mined {miner.calls}"
    )


async def test_a_lane_with_no_identity_falls_back_to_the_lane(tmp_db: DbPool) -> None:
    """A runner lane has no person behind it, and must still be minable."""
    await _write_transcript(tmp_db)
    miner = FakeMiner()

    await _handler(tmp_db, miner, FakeRegistry(FakeProvider(_notable()))).execute(
        _job(identity_key=None)
    )

    assert miner.calls == [LANE]


# ----------------------------------------------------------------- the artifact


async def test_the_summary_is_staged_where_recall_will_find_it(tmp_db: DbPool) -> None:
    await _write_transcript(tmp_db)
    handler = _handler(tmp_db, FakeMiner(), FakeRegistry(FakeProvider(_notable())))

    await handler.execute(_job())

    rows = await tmp_db.fetch_all(
        "SELECT content, source_type, source_ref, confidence, trust FROM staged_facts"
        " WHERE source_type = 'conversation_summary'"
    )
    assert len(rows) == 1
    assert rows[0]["source_ref"] == IDENTITY, "filed under the PERSON, not the lane"
    assert "part 5" in rows[0]["content"]
    # Authored, not inferred: it must clear the promoter's confidence gate, which
    # is what makes it reachable at all (see part 5a).
    assert rows[0]["confidence"] >= 0.8
    assert rows[0]["trust"] == "self"


async def test_the_model_decides_notability(tmp_db: DbPool) -> None:
    """Q14's gate moved INTO the call. notable=false stages nothing, and says so
    rather than staging an empty summary."""
    await _write_transcript(tmp_db)
    provider = FakeProvider(_NOT_NOTABLE)

    result = await _handler(tmp_db, FakeMiner(), FakeRegistry(provider)).execute(_job())

    assert result.success is True
    assert provider.prompts, "the model was asked"
    rows = await tmp_db.fetch_all(
        "SELECT 1 FROM staged_facts WHERE source_type = 'conversation_summary'"
    )
    assert not rows, "nothing notable must stage nothing"


async def test_unparseable_output_stages_nothing_and_is_not_silent(
    tmp_db: DbPool,
) -> None:
    """A model that ignores the schema must not have its prose stored as a fact."""
    await _write_transcript(tmp_db)
    result = await _handler(
        tmp_db, FakeMiner(), FakeRegistry(FakeProvider("I think it went well!")),
    ).execute(_job())

    rows = await tmp_db.fetch_all(
        "SELECT 1 FROM staged_facts WHERE source_type = 'conversation_summary'"
    )
    assert not rows
    assert result.metadata.get("summary_parsed") is False


async def test_the_standard_tier_is_named_explicitly(tmp_db: DbPool) -> None:
    """Q19's final answer, after tier_selector was proven unable to serve this."""
    await _write_transcript(tmp_db)
    registry = FakeRegistry(FakeProvider(_notable()))

    await _handler(tmp_db, FakeMiner(), registry).execute(_job())

    assert registry.tiers == ["standard"]


async def test_the_whole_incarnation_is_read_not_a_tail(tmp_db: DbPool) -> None:
    """A day is the unit being summarised. Reading a fixed tail would summarise
    the end of the day and call it the day."""
    await _write_transcript(tmp_db, turns=30)
    provider = FakeProvider(_notable())

    await _handler(tmp_db, FakeMiner(), FakeRegistry(provider)).execute(_job())

    assert "question 0" in provider.prompts[0], "the earliest turn must be present"
    assert "question 29" in provider.prompts[0]


async def test_another_owners_rows_never_enter_the_transcript(tmp_db: DbPool) -> None:
    """D01.7 cleanup — the transcript read is scoped to the CONVERSATION'S owner.

    `messages` is an owner-governed table, and this read matched on
    `conversation_id` alone. A row carrying the right conversation_id but a
    different owner_id would have been summarised into someone else's day.

    Scoped via the conversation's OWN owner rather than a constructor default:
    a default would read nothing for every non-default principal, which is the
    silent no-op failure this item keeps finding (see
    any_active_task_for_lane's docstring for the same trap).
    """
    await _write_transcript(tmp_db, turns=1)
    # A foreign row that claims the same conversation.
    await tmp_db.execute(
        "INSERT INTO messages (id, conversation_id, role, content, model,"
        " created_at, trace_id, owner_id) VALUES (?, ?, ?, ?, NULL, ?, '', ?)",
        (str(uuid.uuid4()), ENDED, "user", "SOMEONE ELSES SECRET",
         datetime.datetime.now(datetime.UTC).isoformat(), "principal-intruder"),
    )
    provider = FakeProvider(_notable())

    await _handler(tmp_db, FakeMiner(), FakeRegistry(provider)).execute(_job())

    assert "SOMEONE ELSES SECRET" not in provider.prompts[0]
    assert "question 0" in provider.prompts[0]  # the real transcript still reads


# ------------------------------------------------------- durability and honesty


async def test_the_boundary_leaves_a_durable_task_record(tmp_db: DbPool) -> None:
    """Bakir's Q15, reaffirmed: the job creates a DurableTask so a summary that
    dies mid-flight is resumable rather than restarted from nothing."""
    await _write_transcript(tmp_db)

    await _handler(tmp_db, FakeMiner(), FakeRegistry(FakeProvider(_notable()))).execute(
        _job()
    )

    rows = await tmp_db.fetch_all(
        "SELECT status, goal, owner_id FROM tasks WHERE goal LIKE '%rollover%'"
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["owner_id"] == IDENTITY


async def test_a_provider_failure_fails_the_job_honestly(tmp_db: DbPool) -> None:
    """It must not report success having written nothing — and the mining that DID
    happen must still be reported, not thrown away."""
    await _write_transcript(tmp_db)
    miner = FakeMiner()

    result = await _handler(
        tmp_db, miner, FakeRegistry(FakeProvider(""), raises=True),
    ).execute(_job())

    assert result.success is False
    assert result.error
    assert miner.calls == [IDENTITY], "the miner ran before the summary and counts"
    assert result.metadata.get("mined") == 3


async def test_a_provider_failure_leaves_the_task_failed_not_running(
    tmp_db: DbPool,
) -> None:
    """A task row stuck in 'running' for ever is the zombie this record exists to
    prevent."""
    await _write_transcript(tmp_db)

    await _handler(
        tmp_db, FakeMiner(), FakeRegistry(FakeProvider(""), raises=True),
    ).execute(_job())

    rows = await tmp_db.fetch_all(
        "SELECT status FROM tasks WHERE goal LIKE '%rollover%'"
    )
    assert rows and rows[0]["status"] == "failed"


async def test_a_job_missing_its_incarnation_fails_loudly(tmp_db: DbPool) -> None:
    """Never guess which conversation ended. A malformed job is a bug report."""
    result = await _handler(
        tmp_db, FakeMiner(), FakeRegistry(FakeProvider(_notable())),
    ).execute(_job(ended_session_id=""))

    assert result.success is False
    assert result.error


# --------------------------------------------------------------- the subscriber
#
# The event → durable-work bridge. Bakir's Q15: a rollover fires at 4 AM
# unattended, so the consumer must ENQUEUE rather than work inline — anything done
# in the handler thread is lost if the process dies, which is precisely when
# nobody is watching.


class _Bus:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def subscribe(self, event: str, handler) -> None:  # noqa: ANN001
        self.handlers.setdefault(event, []).append(handler)

    async def fire(self, event: str, payload: dict) -> None:
        for h in self.handlers.get(event, []):
            await h(payload)


def _payload(**overrides: object) -> dict:
    p: dict = {
        "session_key": LANE,
        "old_session_id": ENDED,
        "new_session_id": "20260726_040001_bbbbbbbb",
        "reason": "daily",
        "owl_name": "Brain",
        "channel": "telegram",
        "identity_key": IDENTITY,
        "message_count": 4,
        "completed_turns": 2,
    }
    p.update(overrides)
    return p


async def test_a_rollover_enqueues_one_durable_job(tmp_db: DbPool) -> None:
    from stackowl.memory.rollover_summary_handler import register_rollover_consumer
    from stackowl.sessions.store import SessionStore

    bus = _Bus()
    register_rollover_consumer(bus, tmp_db)
    await bus.fire(SessionStore.ROLLOVER_EVENT, _payload())

    rows = await tmp_db.fetch_all(
        "SELECT handler_name, idempotency_key, status, params FROM jobs"
    )
    assert len(rows) == 1
    assert rows[0]["handler_name"] == "rollover_summary"
    assert rows[0]["idempotency_key"] == f"rollover:{LANE}:{ENDED}"
    assert rows[0]["status"] == "pending"
    params = json.loads(rows[0]["params"])
    assert params["ended_session_id"] == ENDED
    assert params["identity_key"] == IDENTITY


async def test_run_once_is_set_so_the_job_cannot_become_recurring(
    tmp_db: DbPool,
) -> None:
    """The scheduler decides recurring-vs-one-shot from params['run_once'].

    Without it a boundary's summary job would re-arm onto a cadence for ever and
    re-summarise the same ended conversation.
    """
    from stackowl.memory.rollover_summary_handler import register_rollover_consumer
    from stackowl.sessions.store import SessionStore

    bus = _Bus()
    register_rollover_consumer(bus, tmp_db)
    await bus.fire(SessionStore.ROLLOVER_EVENT, _payload())

    rows = await tmp_db.fetch_all("SELECT params FROM jobs")
    assert json.loads(rows[0]["params"])["run_once"] is True


async def test_one_boundary_enqueues_once_even_if_announced_twice(
    tmp_db: DbPool,
) -> None:
    """Idempotency is enforced by the DB (jobs.idempotency_key is UNIQUE), not by
    hoping the event fires once."""
    from stackowl.memory.rollover_summary_handler import register_rollover_consumer
    from stackowl.sessions.store import SessionStore

    bus = _Bus()
    register_rollover_consumer(bus, tmp_db)
    await bus.fire(SessionStore.ROLLOVER_EVENT, _payload())
    await bus.fire(SessionStore.ROLLOVER_EVENT, _payload())

    rows = await tmp_db.fetch_all("SELECT job_id FROM jobs")
    assert len(rows) == 1, "a duplicate announcement must not double-summarise"


async def test_a_payload_without_an_ended_incarnation_enqueues_nothing(
    tmp_db: DbPool,
) -> None:
    """The sweeper publishes new_session_id=None; a MISSING OLD id means there is
    no conversation to summarise, and inventing one is worse than skipping."""
    from stackowl.memory.rollover_summary_handler import register_rollover_consumer
    from stackowl.sessions.store import SessionStore

    bus = _Bus()
    register_rollover_consumer(bus, tmp_db)
    await bus.fire(SessionStore.ROLLOVER_EVENT, _payload(old_session_id=""))

    assert await tmp_db.fetch_all("SELECT job_id FROM jobs") == []


async def test_a_failing_enqueue_never_breaks_the_boundary(tmp_db: DbPool) -> None:
    """A rollover must complete even if its consumer cannot enqueue. The
    conversation starting is more important than the summary."""
    from stackowl.memory.rollover_summary_handler import register_rollover_consumer
    from stackowl.sessions.store import SessionStore

    class _BrokenDb:
        async def execute(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            raise RuntimeError("disk on fire")

        async def fetch_all(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            raise RuntimeError("disk on fire")

    bus = _Bus()
    register_rollover_consumer(bus, _BrokenDb())
    # Must not raise.
    await bus.fire(SessionStore.ROLLOVER_EVENT, _payload())


async def test_the_sweeper_shape_of_the_event_is_accepted(tmp_db: DbPool) -> None:
    """The sweeper finalises WITHOUT minting, so it publishes new_session_id=None.
    That is a real boundary and must still be summarised."""
    from stackowl.memory.rollover_summary_handler import register_rollover_consumer
    from stackowl.sessions.store import SessionStore

    bus = _Bus()
    register_rollover_consumer(bus, tmp_db)
    await bus.fire(SessionStore.ROLLOVER_EVENT, _payload(new_session_id=None))

    rows = await tmp_db.fetch_all("SELECT idempotency_key FROM jobs")
    assert len(rows) == 1
