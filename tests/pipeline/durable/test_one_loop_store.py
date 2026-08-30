"""The ONE loop's durable half — claim, fail-with-learning, deliver, prune.

BAKIR's architecture, 2026-08-17. Every requirement below is his, quoted where the
wording decides the behaviour:

* "whatever triggering in the platform, it's a task"
* "if in table we have five pending, five loops parallel... there's no ordering"
* "if it fails, again moving back to pending and adding previous failure details.
  So next loop when it picks it, it also looks: is there any previous one? Yes —
  learn from that experience"
* "each task we may have around thirty limit to try. And this thirty can be in
  configuration"
* "if it's delivered to me, it means loop is completed"
* "delete completed jobs older than one day"

These tests pin the STORE. The loop that drives it is a separate slice; keeping
them apart is what lets the claim semantics be proven without a running event loop.

WHY CLAIMING IS THE HARD PART. Two workers must never run one row. The claim is a
compare-and-set — ``UPDATE ... WHERE status='pending'`` — so exactly one caller can
win, which is the same shape scheduler.py already uses in production for jobs. A
claim that merely SELECTs and then UPDATEs would double-run under concurrency, and
double-running a task that sends a message sends it twice.
"""

from __future__ import annotations

import asyncio
import datetime

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio

UTC = datetime.UTC


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))

    db = DbPool(db_path=tmp_path / "t.db")
    await db.open()
    seed_schema(tmp_path / "t.db")
    yield DurableTaskStore(db)
    await db.close()


async def _pending(store: DurableTaskStore, task_id: str, **over: object) -> None:
    """Enqueue a task the loop is allowed to pick up."""
    await store.enqueue(
        DurableTask(
            task_id=task_id,
            goal=str(over.pop("goal", "answer the question")),
            status="pending",
            **over,  # type: ignore[arg-type]
        )
    )


class TestEveryTriggerCanBecomeARow:
    async def test_a_task_is_enqueued_pending_and_claimable(
        self, store: DurableTaskStore
    ) -> None:
        await _pending(store, "t1", destination="telegram:72055773",
                       achievement="the answer is delivered to telegram")

        claimable = await store.claimable(limit=10)

        assert [t.task_id for t in claimable] == ["t1"]
        assert claimable[0].destination == "telegram:72055773"

    async def test_the_trigger_kind_is_recorded(self, store: DurableTaskStore) -> None:
        """chat / schedule / subgoal / incident — so the loop can be asked what it
        is serving rather than that being guessed from the goal text."""
        await _pending(store, "t1", trigger_kind="chat")

        assert (await store.claimable(limit=10))[0].trigger_kind == "chat"


class TestClaimingIsSafeUnderConcurrency:
    async def test_only_one_worker_can_claim_a_row(self, store: DurableTaskStore) -> None:
        """The property that makes parallel workers safe. Both call at once; exactly
        one wins. Without a CAS claim both would run, and a task that sends a message
        would send it twice."""
        await _pending(store, "t1")

        won = await asyncio.gather(
            store.claim("t1", worker="w1", lease_seconds=30),
            store.claim("t1", worker="w2", lease_seconds=30),
        )

        assert sorted(won) == [False, True], f"expected exactly one winner, got {won}"

    async def test_a_claimed_row_is_no_longer_claimable(
        self, store: DurableTaskStore
    ) -> None:
        await _pending(store, "t1")
        await store.claim("t1", worker="w1", lease_seconds=30)

        assert await store.claimable(limit=10) == []

    async def test_five_pending_rows_are_all_offered_at_once(
        self, store: DurableTaskStore
    ) -> None:
        """"five pending, five loops parallel... there's no ordering." The store's
        job is to offer all five; running them concurrently is the loop's job."""
        for i in range(5):
            await _pending(store, f"t{i}")

        assert len(await store.claimable(limit=10)) == 5


class TestCrashedWorkDoesNotLeak:
    async def test_an_expired_lease_returns_the_row_to_pending(
        self, store: DurableTaskStore
    ) -> None:
        """A worker that dies mid-task leaves the row 'running' forever, and no loop
        would ever pick it up again — work lost SILENTLY, which is the worst failure
        mode because nothing reports it. The lease expiry is what makes a crash
        recoverable."""
        await _pending(store, "t1")
        await store.claim("t1", worker="w1", lease_seconds=-1)  # already expired

        reclaimed = await store.reclaim_expired()

        assert reclaimed == 1
        assert [t.task_id for t in await store.claimable(limit=10)] == ["t1"]

    async def test_a_live_lease_is_not_reclaimed(self, store: DurableTaskStore) -> None:
        await _pending(store, "t1")
        await store.claim("t1", worker="w1", lease_seconds=300)

        assert await store.reclaim_expired() == 0


class TestFailureTeachesTheNextAttempt:
    async def test_a_failure_returns_to_pending_carrying_what_failed(
        self, store: DurableTaskStore
    ) -> None:
        """Bakir: "adding previous failure or action details. So next loop when it
        picks it, it also looks: is there any previous one?" The record rides the
        row, so the next attempt is constrained rather than blind."""
        await _pending(store, "t1")
        await store.claim("t1", worker="w1", lease_seconds=30)

        await store.fail_and_requeue(
            "t1", error="web_search returned nothing", failure_class="capability",
            banned=("web_search",),
        )

        again = (await store.claimable(limit=10, now=_later()))[0]
        assert again.attempt_count == 1
        assert again.last_error == "web_search returned nothing"
        assert again.last_failure_class == "capability"
        assert again.banned_capabilities == ("web_search",)

    async def test_bans_ACCUMULATE_across_attempts(self, store: DurableTaskStore) -> None:
        """Attempt three must know what attempts one AND two already burned.
        Overwriting would let the loop rediscover a dead route forever."""
        await _pending(store, "t1")
        await store.fail_and_requeue("t1", error="a", failure_class="capability",
                                     banned=("web_search",))
        await store.fail_and_requeue("t1", error="b", failure_class="capability",
                                     banned=("browser_navigate",))

        again = (await store.claimable(limit=10, now=_later()))[0]
        assert set(again.banned_capabilities) == {"web_search", "browser_navigate"}

    async def test_a_requeued_task_backs_off_before_it_is_claimable_again(
        self, store: DurableTaskStore
    ) -> None:
        """Without backoff a failing row is re-claimed on the very next 5-second
        tick, turning one broken task into a hot loop."""
        await _pending(store, "t1")
        await store.fail_and_requeue("t1", error="boom", failure_class="transient")

        assert await store.claimable(limit=10) == [], "requeued with no backoff"
        assert len(await store.claimable(limit=10, now=_later())) == 1


class TestTheCeilingIsRealAndConfigurable:
    async def test_hitting_max_attempts_dead_letters_instead_of_looping(
        self, store: DurableTaskStore
    ) -> None:
        """"thirty limit to try." A loop whose only exit is success burns money
        forever; the ceiling is what stops it."""
        await _pending(store, "t1", max_attempts=3)

        for _ in range(3):
            await store.fail_and_requeue("t1", error="nope", failure_class="permanent")

        assert await store.claimable(limit=10, now=_later()) == []
        assert (await store.get("t1")).status == "dead_letter"

    async def test_the_ceiling_is_per_task(self, store: DurableTaskStore) -> None:
        """"this thirty can be in configuration" — so it is a column, not a constant
        compiled into the loop."""
        await _pending(store, "cheap", max_attempts=1)
        await _pending(store, "patient", max_attempts=30)

        await store.fail_and_requeue("cheap", error="x", failure_class="permanent")
        await store.fail_and_requeue("patient", error="x", failure_class="transient")

        assert (await store.get("cheap")).status == "dead_letter"
        assert (await store.get("patient")).status == "pending"


class TestDoneMeansDelivered:
    async def test_a_task_completes_only_when_its_outcome_LANDED(
        self, store: DurableTaskStore
    ) -> None:
        """The rule that makes the whole loop trustworthy. Bakir: "if it's delivered
        to me, it means loop is completed." Marking success from the function's
        return value is a self-report — the overclaim shape this platform already
        pays for elsewhere."""
        await _pending(store, "t1", destination="telegram:72055773")

        await store.mark_delivered("t1", result="Your name is Friday.")

        t = await store.get("t1")
        assert t.status == "completed"
        assert t.delivered_at is not None
        assert t.result == "Your name is Friday."

    async def test_a_delivered_task_is_never_re_claimed(
        self, store: DurableTaskStore
    ) -> None:
        await _pending(store, "t1")
        await store.mark_delivered("t1", result="done")

        assert await store.claimable(limit=10, now=_later()) == []


class TestTheGraphIsEdgesBetweenRows:
    async def test_a_task_waits_for_its_dependencies(
        self, store: DurableTaskStore
    ) -> None:
        """"one loop may need small other loops." A sub-task is another ROW; the
        parent is not claimable until its children have landed."""
        await _pending(store, "child")
        await _pending(store, "parent", depends_on=("child",))

        claimable = {t.task_id for t in await store.claimable(limit=10)}

        assert claimable == {"child"}, "the parent ran before its dependency"

    async def test_the_parent_becomes_claimable_once_the_child_lands(
        self, store: DurableTaskStore
    ) -> None:
        await _pending(store, "child")
        await _pending(store, "parent", depends_on=("child",))
        await store.mark_delivered("child", result="sub-answer")

        claimable = {t.task_id for t in await store.claimable(limit=10)}

        assert claimable == {"parent"}

    async def test_a_dead_lettered_child_does_not_strand_the_parent_silently(
        self, store: DurableTaskStore
    ) -> None:
        """A parent blocked forever on a child that will never succeed is leaked
        work wearing a different hat. It dead-letters too, so it is visible."""
        await _pending(store, "child", max_attempts=1)
        await _pending(store, "parent", depends_on=("child",))
        await store.fail_and_requeue("child", error="x", failure_class="permanent")

        assert (await store.get("child")).status == "dead_letter"
        assert await store.claimable(limit=10, now=_later()) == []
        assert (await store.get("parent")).status == "dead_letter"


class TestCompletedWorkIsPruned:
    async def test_completed_rows_older_than_a_day_are_deleted(
        self, store: DurableTaskStore
    ) -> None:
        """Bakir asked for this explicitly. Note what it does NOT touch below."""
        await _pending(store, "old")
        await store.mark_delivered("old", result="done")
        await store._db.execute(  # noqa: SLF001 — age it past the window
            "UPDATE tasks SET updated_at = datetime('now','-2 day') WHERE task_id='old'"
        )
        await _pending(store, "fresh")
        await store.mark_delivered("fresh", result="done")

        pruned = await store.prune_completed(older_than_days=1)

        assert pruned == 1
        assert (await store.get("fresh")).task_id == "fresh"

    async def test_a_DEAD_LETTER_is_never_pruned(self, store: DurableTaskStore) -> None:
        """Pruning a dead letter would delete the one record of work that failed
        permanently — the thing the operator most needs to see."""
        await _pending(store, "dead", max_attempts=1)
        await store.fail_and_requeue("dead", error="x", failure_class="permanent")
        await store._db.execute(  # noqa: SLF001
            "UPDATE tasks SET updated_at = datetime('now','-9 day') WHERE task_id='dead'"
        )

        assert await store.prune_completed(older_than_days=1) == 0
        assert (await store.get("dead")).status == "dead_letter"


def _later() -> datetime.datetime:
    """Past any backoff the store applies."""
    return datetime.datetime.now(UTC) + datetime.timedelta(hours=1)


class TestTheThreeAdditionsAreHONEST:
    """These three were DESCRIBED as finished before they were. Each test pins the
    claim the design made, so the description and the behaviour cannot drift again.
    """

    async def test_a_dead_letter_is_ESCALATED_not_merely_logged(
        self, store: DurableTaskStore, monkeypatch
    ) -> None:
        """The design said dead letters are "visible and escalated". They were only
        LOGGED — and a log line nobody tails is the silent give-up this loop exists
        to prevent, wearing the word "visible"."""
        from types import SimpleNamespace

        sent: list[object] = []

        class _Deliverer:
            async def deliver(self, notification: object, **_: object) -> str:
                sent.append(notification)
                return "delivered"

        from stackowl.config.settings import Settings
        from stackowl.pipeline.services import reset_services, set_services

        token = set_services(SimpleNamespace(
            settings=Settings(), proactive_deliverer=_Deliverer(),
        ))
        try:
            await _pending(store, "t1", max_attempts=1,
                           destination="telegram:72055773", goal="find my keys")
            await store.fail_and_requeue("t1", error="nowhere to look",
                                         failure_class="permanent")
        finally:
            reset_services(token)

        assert sent, "the task stopped for good and nobody was told"
        msg = str(getattr(sent[0], "message", ""))
        assert "find my keys" in msg, "the escalation does not say WHICH task"
        assert "nowhere to look" in msg, "the escalation does not say why"
        assert getattr(sent[0], "target", None) == "72055773"

    async def test_escalation_failure_never_changes_the_outcome(
        self, store: DurableTaskStore
    ) -> None:
        """The task has already stopped. Failing to ANNOUNCE it must not also crash
        the caller — but it is logged, because an escalation that fails silently is
        the original bug one level up."""
        from types import SimpleNamespace

        class _Boom:
            async def deliver(self, *_: object, **__: object) -> str:
                raise RuntimeError("channel down")

        from stackowl.config.settings import Settings
        from stackowl.pipeline.services import reset_services, set_services

        token = set_services(SimpleNamespace(
            settings=Settings(), proactive_deliverer=_Boom(),
        ))
        try:
            await _pending(store, "t1", max_attempts=1)
            status = await store.fail_and_requeue("t1", error="x",
                                                  failure_class="permanent")
        finally:
            reset_services(token)

        assert status == "dead_letter"
        assert (await store.get("t1")).status == "dead_letter"

    async def test_permanent_classes_come_from_CONFIG_not_a_constant(
        self, store: DurableTaskStore
    ) -> None:
        """"which failures are truly permanent" is deployment-specific. A class the
        operator has NOT declared permanent must be retried, even if the default
        list would have stopped it."""
        from types import SimpleNamespace

        from stackowl.config.settings import Settings
        from stackowl.pipeline.services import reset_services, set_services

        cfg = Settings()
        narrowed = cfg.model_copy(update={
            "task_loop": cfg.task_loop.model_copy(
                update={"permanent_failure_classes": ("only_this",)}
            )
        })
        token = set_services(SimpleNamespace(settings=narrowed,
                                             proactive_deliverer=None))
        try:
            await _pending(store, "t1", max_attempts=10)
            # 'auth' is permanent by DEFAULT but not in this deployment's list.
            await store.fail_and_requeue("t1", error="x", failure_class="auth")
        finally:
            reset_services(token)

        assert (await store.get("t1")).status == "pending", (
            "a class the operator did not declare permanent was treated as fatal"
        )

    async def test_a_cascaded_dead_letter_NAMES_the_child_that_failed(
        self, store: DurableTaskStore
    ) -> None:
        """A parent that reads "a dependency will never land" leaves the operator to
        work out WHICH one across a graph. It names it."""
        await _pending(store, "child", max_attempts=1)
        await _pending(store, "parent", depends_on=("child",))
        await store.fail_and_requeue("child", error="x", failure_class="permanent")

        await store.claimable(limit=10, now=_later())  # drives the cascade

        parent = await store.get("parent")
        assert parent.status == "dead_letter"
        assert "child" in (parent.last_error or ""), parent.last_error
        assert parent.last_failure_class == "dependency_failed"


class TestATaskThatOwedAnAnswerIsNeverLeftDead:
    """BAKIR, 2026-08-18. Task 43be4591 asked Friday to create an agent. It hit the
    step budget, was written ``status='failed'`` with ``destination='telegram:...'``
    and ``delivered_at`` NULL, and nothing ever picked it up. He got silence.

    The chokepoint fix stops NEW rows dying this way but cannot reach rows already
    dead — the loop claims only 'pending'. This sweep is what reaches them, and its
    predicate is deliberately narrow: a destination, and no delivery. On the live
    table that matched exactly one row out of 850 failures.
    """

    async def test_a_failed_task_with_someone_waiting_is_requeued(
        self, store: DurableTaskStore,
    ) -> None:
        await _pending(store, "owed")
        await store.update_status("owed", "running")
        await store._execute_owned(
            "UPDATE tasks SET status='failed', destination='telegram:72055773', "
            "result='budget:stop:steps:limit=20.0:actual=20.0' "
            "WHERE owner_id=? AND task_id=?",
            [store._owner_id, "owed"],
        )

        assert await store.revive_undelivered_failures() == 1

        row = await store.get("owed")
        assert row.status == "pending"
        assert row.delivered_at is None

    async def test_the_revived_task_carries_what_killed_it(
        self, store: DurableTaskStore,
    ) -> None:
        """Requeuing without the reason is the blind retry the design rejects — it
        would spend the same twenty steps and stop in the same place."""
        await _pending(store, "owed2")
        await store._execute_owned(
            "UPDATE tasks SET status='failed', destination='telegram:1', "
            "result='budget:stop:steps:limit=20.0:actual=20.0' "
            "WHERE owner_id=? AND task_id=?",
            [store._owner_id, "owed2"],
        )

        await store.revive_undelivered_failures()

        row = await store.get("owed2")
        assert "budget:stop" in (row.last_error or "")
        assert row.last_failure_class == "budget"

    async def test_a_failure_with_nobody_waiting_is_left_alone(
        self, store: DurableTaskStore,
    ) -> None:
        """The guard against a stampede. Sweeps and internal sub-tasks have no
        destination and no one expecting anything, so reviving them would re-run
        hundreds of rows to no benefit."""
        await _pending(store, "nobody")
        await store._execute_owned(
            "UPDATE tasks SET status='failed' WHERE owner_id=? AND task_id=?",
            [store._owner_id, "nobody"],
        )

        assert await store.revive_undelivered_failures() == 0
        assert (await store.get("nobody")).status == "failed"

    async def test_an_already_delivered_task_is_not_resurrected(
        self, store: DurableTaskStore,
    ) -> None:
        """Delivered is the achievement. Re-running it would send a second answer
        to a question already answered."""
        await _pending(store, "done")
        await store._execute_owned(
            "UPDATE tasks SET status='failed', destination='telegram:1', "
            "delivered_at='2026-08-18T00:00:00+00:00' "
            "WHERE owner_id=? AND task_id=?",
            [store._owner_id, "done"],
        )

        assert await store.revive_undelivered_failures() == 0

    async def test_a_dead_letter_stays_dead(
        self, store: DurableTaskStore,
    ) -> None:
        """dead_letter is a decision the loop already made AND announced to the
        operator. Quietly undoing it would re-run work they were told had stopped."""
        await _pending(store, "dead")
        await store._execute_owned(
            "UPDATE tasks SET status='dead_letter', destination='telegram:1' "
            "WHERE owner_id=? AND task_id=?",
            [store._owner_id, "dead"],
        )

        assert await store.revive_undelivered_failures() == 0
        assert (await store.get("dead")).status == "dead_letter"

    async def test_a_revived_task_starts_clean_not_mid_transcript(
        self, store: DurableTaskStore,
    ) -> None:
        """MEASURED LIVE 2026-08-19. The first row this sweep ever revived was
        claimed by the loop within seconds and failed instantly with
        "ResumeTranscriptError: Invalid resume transcript" — it still carried the
        checkpoint of the run that had died at step 12. Resuming a corpse fails the
        same way every time, so it would have burned all thirty attempts and
        dead-lettered without ever delivering. A revived task is a fresh attempt at
        the GOAL."""
        await _pending(store, "stale")
        await store.save_checkpoint("stale", b"a transcript from the run that died")
        await store._execute_owned(
            "UPDATE tasks SET status='failed', destination='telegram:1', "
            "current_step=12, result='budget:stop:steps:limit=20.0:actual=20.0' "
            "WHERE owner_id=? AND task_id=?",
            [store._owner_id, "stale"],
        )

        await store.revive_undelivered_failures()

        row = await store.get("stale")
        assert row.status == "pending"
        assert row.current_step == 0
        assert await store.load_checkpoint("stale") is None


class TestACorruptCheckpointIsNotRepeatedForever:
    async def test_a_resume_failure_drops_the_checkpoint_on_requeue(
        self, store: DurableTaskStore,
    ) -> None:
        """Not only the revive path. ANY task whose checkpoint stops validating
        would otherwise fail identically at resume until the ceiling."""
        await _pending(store, "corrupt")
        await store.save_checkpoint("corrupt", b"no longer valid")

        status = await store.fail_and_requeue(
            "corrupt",
            error="execute: ResumeTranscriptError: Invalid resume transcript",
            failure_class="corrupt_state",
        )

        assert status == "pending"
        assert await store.load_checkpoint("corrupt") is None
        assert (await store.get("corrupt")).current_step == 0

    async def test_an_ordinary_failure_KEEPS_its_checkpoint(
        self, store: DurableTaskStore,
    ) -> None:
        """The partial progress is the point of checkpointing. Dropping it on every
        failure would make each retry redo work that had already succeeded."""
        await _pending(store, "ordinary")
        await store.save_checkpoint("ordinary", b"still good")

        await store.fail_and_requeue(
            "ordinary", error="connection reset", failure_class="transient",
        )

        kept = await store.load_checkpoint("ordinary")
        assert kept is not None
        # Compared loosely: this store round-trips the blob through TEXT, so
        # the exact type back is not the contract — surviving the requeue is.
        assert b"still good" in (kept if isinstance(kept, bytes) else kept.encode())


class TestEveryAgentActionGetsTheLoopContract:
    """BAKIR, 2026-08-19: "if agent all actions integrate with core loop logic agent
    will get that superpower in doing everything."

    MEASURED that day, and it was not true yet:

        rows by trigger_kind:  chat 39,  (none) 1058
        rows carrying a destination:     39   — all of them chat

    `trigger_kind="schedule"` appears in task_runner.py and `"subgoal"` in
    decompose.py, and ZERO rows had ever carried either. The reason is a silent
    drop: `create()` inserted a fixed column list that omitted destination,
    achievement, trigger_kind, max_attempts, depends_on and idempotency_key.
    `enqueue()` hid it by issuing a second UPDATE right after `create()` to set them
    — so the ONE caller that used enqueue (chat turns) worked, and every other
    caller built a row with those fields set on the model and had them thrown away
    on write.

    The consequence is not cosmetic. A row with no destination cannot be rescued by
    `revive_undelivered_failures` (which requires one), cannot be warned about by
    `_warn_if_undelivered`, and reaches 'completed' with delivered_at NULL — success
    claimed with nobody proven to have received it. That is precisely the guarantee
    the loop exists to provide, and scheduled work never had it.
    """

    async def test_create_persists_the_destination(
        self, store: DurableTaskStore,
    ) -> None:
        await store.create(DurableTask(
            task_id="sched-1", owner_id=store._owner_id, goal="daily digest",
            status="running", destination="telegram:72055773",
            achievement="the answer is delivered to the job's targets",
            trigger_kind="schedule",
        ))

        row = await store.get("sched-1")
        assert row.destination == "telegram:72055773"
        assert row.achievement == "the answer is delivered to the job's targets"
        assert row.trigger_kind == "schedule"

    async def test_a_scheduled_row_can_now_be_rescued_when_it_never_delivers(
        self, store: DurableTaskStore,
    ) -> None:
        """The whole point of stamping it. Before, a failed scheduled job was
        invisible to the sweep because the sweep keys on having a destination."""
        await store.create(DurableTask(
            task_id="sched-2", owner_id=store._owner_id, goal="daily digest",
            status="running", destination="telegram:72055773",
            achievement="delivered", trigger_kind="schedule",
        ))
        await store._execute_owned(
            "UPDATE tasks SET status='failed' WHERE owner_id=? AND task_id=?",
            [store._owner_id, "sched-2"],
        )

        assert await store.revive_undelivered_failures() == 1
        assert (await store.get("sched-2")).status == "pending"

    async def test_a_maintenance_row_with_no_destination_is_unchanged(
        self, store: DurableTaskStore,
    ) -> None:
        """A sweep or prune has nobody waiting. It must NOT gain a delivery
        obligation, or every housekeeping handler dead-letters."""
        await store.create(DurableTask(
            task_id="sweep-1", owner_id=store._owner_id, goal="prune",
            status="running",
        ))

        row = await store.get("sweep-1")
        assert row.destination is None
        assert row.achievement is None

    async def test_enqueue_still_round_trips_everything(
        self, store: DurableTaskStore,
    ) -> None:
        """enqueue's follow-up UPDATE and create must not disagree about a row."""
        await store.enqueue(DurableTask(
            task_id="enq-1", owner_id=store._owner_id, goal="ask a question",
            status="pending", destination="telegram:1", achievement="delivered",
            trigger_kind="chat", max_attempts=7, idempotency_key="k1",
        ))

        row = await store.get("enq-1")
        assert row.destination == "telegram:1"
        assert row.trigger_kind == "chat"
        assert row.max_attempts == 7


class TestASubTaskDiesWithItsParent:
    """MEASURED LIVE 2026-08-19, while Bakir was asking why agents keep failing.

    Row `child-a49e8ce9…` — "List the existing owls so I can check for a name
    collision before minting 'mailbutler'" — sat at attempt_count 13 of 30, status
    pending, destination None, last_error "retry did not deliver (actuator reported
    'pending')". Its parent, 43be4591, was already **completed and delivered**.

    So the loop was spending a model call every few minutes re-running a
    name-collision check for an owl that had been created hours earlier, and had 17
    attempts still to burn. Each pass also logged
    `retry_queue_store.mark_attempt_failed: row not found`, because a loop-born task
    has no row in the retry queue the actuator does its bookkeeping in.

    THE RULE THIS ADDS. A sub-task exists to serve its parent. Once that parent is
    terminal, the child has no destination, no achievement, and nobody waiting — so
    it is not claimable. This is the same principle as `_deps_satisfied` (do not run
    what cannot help), applied upward instead of sideways.

    A child whose parent is still RUNNING is untouched: that is the ordinary
    fan-out the graph exists for.
    """

    async def _child_of(self, store: DurableTaskStore, parent_status: str) -> str:
        await _pending(store, "par-1")
        await store._execute_owned(
            "UPDATE tasks SET status=? WHERE owner_id=? AND task_id=?",
            [parent_status, store._owner_id, "par-1"],
        )
        await _pending(store, "kid-1")
        await store._execute_owned(
            "UPDATE tasks SET parent_task_id='par-1' WHERE owner_id=? AND task_id=?",
            [store._owner_id, "kid-1"],
        )
        return "kid-1"

    async def test_a_child_of_a_completed_parent_is_not_claimable(
        self, store: DurableTaskStore,
    ) -> None:
        kid = await self._child_of(store, "completed")

        ids = {t.task_id for t in await store.claimable(limit=50)}

        assert kid not in ids, "the parent already delivered; the child has no work"

    async def test_a_child_of_a_dead_lettered_parent_is_not_claimable(
        self, store: DurableTaskStore,
    ) -> None:
        """The parent stopped for good and the operator was told. Grinding its
        children afterwards spends budget on work nobody is waiting for."""
        kid = await self._child_of(store, "dead_letter")

        ids = {t.task_id for t in await store.claimable(limit=50)}

        assert kid not in ids

    async def test_a_child_of_a_RUNNING_parent_is_still_claimable(
        self, store: DurableTaskStore,
    ) -> None:
        """The ordinary fan-out. Blocking this would break decomposition itself."""
        kid = await self._child_of(store, "running")

        ids = {t.task_id for t in await store.claimable(limit=50)}

        assert kid in ids

    async def test_a_task_with_no_parent_is_unaffected(
        self, store: DurableTaskStore,
    ) -> None:
        await _pending(store, "root-1")

        ids = {t.task_id for t in await store.claimable(limit=50)}

        assert "root-1" in ids
