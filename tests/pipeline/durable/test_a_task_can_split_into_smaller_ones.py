"""The graph half — "one loop may need small other loops" (Bakir, 2026-08-17).

*"Sometimes simple tasks may need multiple small tasks. It's like one loop may need
small other loops."*

WHAT MAKES THIS A GRAPH AND NOT A SECOND SYSTEM. A sub-task is another ROW on the
same table, with a parent and a ``depends_on`` list. The loop already refuses to
claim a row whose dependencies have not landed (slice 1), so the graph is edges
between rows and needs no scheduler of its own.

IT REUSES THE DECOMPOSER THAT WAS ALREADY THERE. ``ObjectiveDecomposer`` turns an
intent into ordered ``SubgoalSpec``s with ``depends_on`` indices and a per-step
complexity estimate, and ``objectives.graph.validate_graph`` rejects cycles with a
three-colour DFS. That is ~2,400 lines which has been running against an empty
table; writing a second decomposer would be the duplication CLAUDE.md forbids.

WHEN A TASK SPLITS, and why not always. Decomposition costs an LLM call and turns
one row into several, so it is a RESPONSE TO EVIDENCE rather than a default: a task
splits when it has failed enough times that repeating it is clearly not working.
Splitting on the first attempt would pay that cost on every trivial task.
"""

from __future__ import annotations

import pytest

from stackowl.objectives.model import SubgoalSpec
from stackowl.pipeline.durable.decompose import (
    plan_subtasks,
    should_decompose,
)
from stackowl.pipeline.durable.task import DurableTask

pytestmark = pytest.mark.asyncio


def _task(**over: object) -> DurableTask:
    base: dict = dict(task_id="parent", goal="plan my trip to Tokyo",
                      status="pending", attempt_count=3, max_attempts=30)
    base.update(over)
    return DurableTask(**base)


class _Decomposer:
    def __init__(self, specs: list[SubgoalSpec] | None = None,
                 boom: bool = False) -> None:
        self._specs = specs or []
        self._boom = boom
        self.calls: list[str] = []

    async def decompose_specs(self, intent: str) -> list[SubgoalSpec]:
        if self._boom:
            raise RuntimeError("provider down")
        self.calls.append(intent)
        return self._specs


class TestWhenATaskSplits:
    async def test_a_task_that_keeps_failing_is_a_candidate(self) -> None:
        """Repeating an approach that has failed three times is the definition of
        the stuck loop this whole design exists to break."""
        assert should_decompose(_task(attempt_count=3)) is True

    async def test_a_task_on_its_first_attempt_is_not(self) -> None:
        """Decomposition costs an LLM call and turns one row into several. Paying
        that on every trivial task would be worse than the problem."""
        assert should_decompose(_task(attempt_count=0)) is False

    async def test_a_task_that_ALREADY_split_does_not_split_again(self) -> None:
        """Otherwise a hard goal recurses into an unbounded fan-out of children,
        each of which fails and splits again."""
        assert should_decompose(_task(attempt_count=5, depends_on=("child-1",))) is False

    async def test_a_CHILD_does_not_split_further(self) -> None:
        """Depth ceiling. A sub-task is meant to be the simple half; letting it
        decompose too is how a two-level plan becomes a runaway tree."""
        assert should_decompose(_task(attempt_count=5, parent_task_id="parent")) is False


class TestTheChildrenAreRealRows:
    async def test_each_subgoal_becomes_a_child_task(self) -> None:
        specs = [SubgoalSpec(description="book flights"),
                 SubgoalSpec(description="book hotel")]
        children = await plan_subtasks(_task(), _Decomposer(specs))

        assert [c.goal for c in children] == ["book flights", "book hotel"]
        assert all(c.parent_task_id == "parent" for c in children)
        assert all(c.trigger_kind == "subgoal" for c in children)

    async def test_declared_dependencies_become_real_edges(self) -> None:
        """The decomposer emits depends_on as INDICES into its own batch; the graph
        needs task ids. A translation slip here silently produces a graph with no
        edges, which runs everything at once and looks fine until order matters."""
        specs = [
            SubgoalSpec(description="research destinations"),
            SubgoalSpec(description="book flights", depends_on=[0]),
        ]
        children = await plan_subtasks(_task(), _Decomposer(specs))

        assert children[0].depends_on == ()
        assert children[1].depends_on == (children[0].task_id,)

    async def test_the_parent_waits_for_every_child(self) -> None:
        """The parent is what delivers the answer, so it must not run until the
        work it delegated has landed."""
        specs = [SubgoalSpec(description="a"), SubgoalSpec(description="b")]
        children = await plan_subtasks(_task(), _Decomposer(specs))

        # The caller sets the parent's depends_on to every child id — that is what
        # makes claimable() hold the parent back until the delegated work lands.
        assert {c.task_id for c in children} == {children[0].task_id,
                                                 children[1].task_id}
        assert len(children) == 2

    async def test_children_inherit_the_parents_destination(self) -> None:
        """A child that lost the destination could not be completed on delivery,
        which is the rule the whole loop turns on."""
        # TWO specs, deliberately: a single-step plan is correctly rejected as a
        # decomposition MISS (see TestItRefusesAnUnsafePlan), so a one-spec fixture
        # here would test the rejection path and never reach inheritance at all.
        specs = [SubgoalSpec(description="a"), SubgoalSpec(description="b")]
        children = await plan_subtasks(
            _task(destination="telegram:72055773", channel="telegram"),
            _Decomposer(specs),
        )

        assert children[0].destination == "telegram:72055773"
        assert children[0].channel == "telegram"


class TestItRefusesAnUnsafePlan:
    async def test_a_CYCLIC_plan_is_rejected_entirely(self) -> None:
        """Two steps waiting on each other never run, and the parent waits for
        both — three rows stuck for ever. Rejecting the whole batch leaves the
        parent to retry normally, which is recoverable."""
        specs = [
            SubgoalSpec(description="a", depends_on=[1]),
            SubgoalSpec(description="b", depends_on=[0]),
        ]

        assert await plan_subtasks(_task(), _Decomposer(specs)) == []

    async def test_an_OUT_OF_RANGE_dependency_is_rejected(self) -> None:
        specs = [SubgoalSpec(description="a", depends_on=[7])]

        assert await plan_subtasks(_task(), _Decomposer(specs)) == []

    async def test_a_single_step_plan_is_not_a_decomposition(self) -> None:
        """The decomposer's documented fail-safe returns ONE spec that IS the whole
        objective. Turning that into a single child would add a row, a hop and a
        delivery boundary while changing nothing."""
        specs = [SubgoalSpec(description="plan my trip to Tokyo")]

        assert await plan_subtasks(_task(), _Decomposer(specs)) == []

    async def test_a_decomposer_that_raises_yields_no_children(self) -> None:
        """A failed split must leave the parent exactly as it was — still
        retryable — never half a plan."""
        assert await plan_subtasks(_task(), _Decomposer(boom=True)) == []

    async def test_no_decomposer_wired_yields_no_children(self) -> None:
        assert await plan_subtasks(_task(), None) == []
