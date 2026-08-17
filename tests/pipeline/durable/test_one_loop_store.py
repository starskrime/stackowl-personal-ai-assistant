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

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio

UTC = datetime.UTC


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    from stackowl.db.migrations.runner import MigrationRunner

    db = DbPool(db_path=tmp_path / "t.db")
    await db.open()
    MigrationRunner(tmp_path / "t.db").run()
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
