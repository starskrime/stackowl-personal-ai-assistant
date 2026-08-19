"""A failed task must go back on the loop, not die where it fell.

BAKIR, 2026-08-18, and he was angry, which was fair. He asked Friday to
"Forget previouse limits and create the agent what i want". Task
``43be4591`` was born with ``destination='telegram:72055773'`` and
``achievement='the reply is delivered to the user who asked'``. It burned twenty
steps, hit the step budget, and was written ``status='failed'`` with
``delivered_at`` NULL. He never got an answer, and NOTHING ever picked it up again.

MEASURED across the whole table at that moment::

    failed     850
    completed  239
    running      1
    rows that have EVER had attempt_count > 0:  0

Zero. The retry-with-learning path had never once executed in production. The
reason is a chokepoint that was only half enforced: ``fail_and_requeue`` — which
records what failed, accumulates ``banned_capabilities`` and puts the row back to
pending — was reachable ONLY from inside ``TaskLoop``. But a chat turn does not run
inside the loop; the fast path produces the reply, and on failure
``executor``/``react_runner``/``task_runner`` each call
``update_status(task_id, "failed")`` directly. ``update_status`` wrote it as
terminal, and the loop only ever claims ``pending``, so the row was unreachable
forever.

WHY THE FIX BELONGS HERE and not in the three callers. ``update_status`` ALREADY
intercepts the mirror-image case one branch above — ``completed`` is checked
against ``_warn_if_undelivered`` precisely because several callers marked rows
complete without proof of delivery. Failure is the same shape and gets the same
treatment: one source, and the other callers ask it. Fixing three call sites
instead would be three copies of one rule, which is the defect this codebase keeps
paying for.

WHY A BLIND REQUEUE WOULD HAVE BEEN WORSE THAN THE BUG. Bakir's design says the
next attempt must be "constrained rather than blind". A step-budget exhaustion
repeated thirty times is thirty identical failures and thirty times the spend. So
the failure is CLASSIFIED on the way in, and a budget exhaustion is what makes
``should_decompose`` true — the retry changes the SHAPE of the work instead of
repeating it.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.durable.failure_class import classify_failure

pytestmark = pytest.mark.asyncio


class TestTheFailureIsClassifiedOnTheWayIn:
    """An unclassified failure is a blind retry, which is the thing being fixed."""

    async def test_a_step_budget_exhaustion_is_recognised(self) -> None:
        """The exact marker from Bakir's stuck task. execute.py writes
        ``budget:stop:{cap}:limit={limit}:actual={actual}``."""
        assert classify_failure("budget:stop:steps:limit=20.0:actual=20.0") == "budget"

    async def test_a_cost_budget_exhaustion_is_also_budget(self) -> None:
        assert classify_failure("budget:stop:cost:limit=1.5:actual=1.5") == "budget"

    async def test_auth_is_permanent_and_must_not_burn_thirty_attempts(self) -> None:
        """"auth" is in the configured permanent set, so classifying it correctly
        is what stops a token problem from retrying for hours."""
        assert classify_failure("401 Unauthorized: invalid api key") == "auth"

    async def test_a_missing_thing_is_not_found(self) -> None:
        assert classify_failure("HTTP 404 Not Found") == "not_found"

    async def test_a_timeout_stays_retryable(self) -> None:
        """A timeout is the CASE FOR retrying. Classifying it as permanent would
        reintroduce the give-up this whole loop exists to prevent."""
        cls = classify_failure("asyncio.TimeoutError: read timed out")
        assert cls == "timeout"

    async def test_an_unrecognised_error_is_left_open_not_guessed_permanent(self) -> None:
        """False "permanent" is the expensive mistake: it strands a recoverable
        task forever, which is exactly the bug being fixed. Unknown must retry."""
        assert classify_failure("something nobody has seen before") == ""

    async def test_empty_input_is_safe(self) -> None:
        assert classify_failure("") == ""
        assert classify_failure(None) == ""


class TestBudgetExhaustionChangesShapeRatherThanRepeating:
    async def test_a_budget_failure_makes_decomposition_the_next_move(self) -> None:
        """``should_decompose`` was DEAD CODE — grep found zero callers outside its
        own module. Wiring it is what makes the retry constrained instead of a
        thirty-fold repeat of the same twenty-step burn."""
        from stackowl.pipeline.durable.failure_class import wants_reshaping

        assert wants_reshaping("budget") is True
        assert wants_reshaping("timeout") is False
        assert wants_reshaping("") is False


class _FakeStore:
    """The narrow store surface ``_maybe_reshape`` touches."""

    def __init__(self) -> None:
        self.enqueued: list[object] = []
        self.deps: dict[str, tuple[str, ...]] = {}

    async def enqueue(self, task: object) -> None:
        self.enqueued.append(task)

    async def set_dependencies(
        self, task_id: str, depends_on: tuple[str, ...],
    ) -> None:
        self.deps[task_id] = depends_on


class _Decomposer:
    def __init__(self, specs: list) -> None:
        self._specs = specs

    async def decompose_specs(self, intent: str) -> list:
        return self._specs


def _loop_with(store: _FakeStore, decomposer: object):
    """A TaskLoop wired to doubles, with the decomposer pinned.

    Built with ``__new__`` deliberately: this exercises ``_maybe_reshape`` alone,
    and running the real constructor would start a ticking loop the test then has
    to chase.
    """
    from stackowl.pipeline.durable.loop import TaskLoop

    loop = TaskLoop.__new__(TaskLoop)
    loop._store = store  # type: ignore[attr-defined]
    loop._decomposer = lambda: decomposer  # type: ignore[assignment,method-assign]
    return loop


def _stuck_task(**over: object):
    """Bakir's row 43be4591 in the shape that matters: out of steps, tried enough."""
    from stackowl.pipeline.durable.task import DurableTask

    base: dict = dict(
        task_id="parent", goal="create the agent the user asked for",
        status="pending", attempt_count=3, max_attempts=30,
        last_failure_class="budget",
        last_error="budget:stop:steps:limit=20.0:actual=20.0",
    )
    base.update(over)
    return DurableTask(**base)


class TestARepeatedlyStuckTaskChangesShape:
    async def test_a_budget_failure_becomes_children_the_parent_waits_on(self) -> None:
        """The whole point. `plan_subtasks`/`should_decompose` had ZERO callers
        outside their own module until this path existed, so a task that ran out of
        steps could only ever run out of steps again."""
        from stackowl.objectives.model import SubgoalSpec

        store = _FakeStore()
        loop = _loop_with(store, _Decomposer([
            SubgoalSpec(description="check the name is free", depends_on=[]),
            SubgoalSpec(description="create the owl", depends_on=[0]),
        ]))

        reshaped = await loop._maybe_reshape(_stuck_task())

        assert reshaped is True
        assert len(store.enqueued) == 2
        assert store.deps["parent"] == tuple(c.task_id for c in store.enqueued)

    async def test_the_reshaped_parent_is_not_also_run_this_tick(self) -> None:
        """True means "handled". Running the parent as well would spend the very
        budget that just ran out, racing the children it just created."""
        from stackowl.objectives.model import SubgoalSpec

        loop = _loop_with(_FakeStore(), _Decomposer([
            SubgoalSpec(description="a", depends_on=[]),
            SubgoalSpec(description="b", depends_on=[0]),
        ]))

        assert await loop._maybe_reshape(_stuck_task()) is True

    async def test_a_timeout_is_retried_as_is_not_split(self) -> None:
        """A timeout is worth repeating unchanged — the network may simply be back.
        Splitting it would spend a model call to solve a problem that isn't shape."""
        store = _FakeStore()
        loop = _loop_with(store, _Decomposer([]))

        assert await loop._maybe_reshape(
            _stuck_task(last_failure_class="timeout")) is False
        assert store.enqueued == []

    async def test_an_early_failure_is_not_split(self) -> None:
        """Splitting on attempt one pays an LLM call and a fan-out on every task
        that would have succeeded on attempt two."""
        store = _FakeStore()
        loop = _loop_with(store, _Decomposer([]))

        assert await loop._maybe_reshape(_stuck_task(attempt_count=1)) is False
        assert store.enqueued == []

    async def test_a_decomposer_that_raises_leaves_the_task_exactly_as_it_was(
        self,
    ) -> None:
        """A rescue step that can strand the work it exists to rescue is worse than
        no rescue step. False = "run it the ordinary way"."""
        class _Boom:
            async def decompose_specs(self, intent: str) -> list:
                raise RuntimeError("provider down")

        store = _FakeStore()
        loop = _loop_with(store, _Boom())

        assert await loop._maybe_reshape(_stuck_task()) is False
        assert store.enqueued == []
        assert store.deps == {}

    async def test_no_decomposer_at_all_is_survivable(self) -> None:
        store = _FakeStore()
        loop = _loop_with(store, None)

        assert await loop._maybe_reshape(_stuck_task()) is False
        assert store.enqueued == []
