"""A task that cannot be done becomes several that can — the graph half.

BAKIR, 2026-08-17: *"Sometimes simple tasks may need multiple small tasks. It's
like one loop may need small other loops."*

WHY THIS IS A GRAPH AND NOT A SECOND SYSTEM. A sub-task is another ROW on the same
table, carrying a parent and a ``depends_on`` list. ``DurableTaskStore.claimable``
already refuses to offer a row whose dependencies have not landed, and already
dead-letters a parent whose dependency failed permanently. So the graph is edges
between rows: no second scheduler, no second status column, no second queue.

IT REUSES THE DECOMPOSER THAT WAS ALREADY IN THE TREE. ``ObjectiveDecomposer``
turns an intent into ordered ``SubgoalSpec``s with ``depends_on`` indices, and
``objectives.graph.validate_graph`` rejects cycles with a three-colour DFS (chosen
there specifically so a legitimate diamond dependency is not false-rejected).
That machinery has been running against an empty table for weeks; writing a second
decomposer would be the duplication CLAUDE.md forbids, and would be worse code
besides.

WHEN A TASK SPLITS. Decomposition costs an LLM call and turns one row into several,
so it is a RESPONSE TO EVIDENCE, never a default: a task splits once it has failed
enough times that repeating the same approach is demonstrably not working. That is
the same signal the retry ladder already uses, applied one rung further out — retry
changes the attempt, substitution changes the tool, decomposition changes the SHAPE
of the work.

THREE THINGS IT REFUSES TO DO, each because the failure is unrecoverable rather
than merely wasteful: it will not split a task that already has children (unbounded
fan-out), will not split a child (depth ceiling), and will not accept a cyclic or
out-of-range plan (rows that wait on each other for ever, with the parent waiting
on both).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.pipeline.durable.task import DurableTask

#: Failed attempts before the loop stops re-running one approach and tries changing
#: the shape of the work instead. Three is where the existing retry ladder already
#: concludes an approach is not working (the react loop guard warns at 3 repeats),
#: so this reuses a threshold the platform has already tuned rather than inventing
#: a second one.
DECOMPOSE_AFTER_ATTEMPTS = 3


def should_decompose(task: DurableTask) -> bool:
    """Is splitting this task the right next move?

    False in every doubtful case. A wrong "no" costs one more ordinary retry; a
    wrong "yes" spends an LLM call and multiplies the row into a fan-out that has
    to be managed, so the asymmetry justifies the conservatism.
    """
    if task.attempt_count < DECOMPOSE_AFTER_ATTEMPTS:
        return False
    if task.depends_on:
        # Already split. Splitting again would fan out without bound: each child
        # fails, splits, and its children split in turn.
        return False
    # Depth ceiling. A sub-task is meant to BE the simple half; letting it
    # decompose is how a two-level plan becomes a runaway tree.
    return not task.parent_task_id


async def plan_subtasks(
    task: DurableTask, decomposer: Any,
) -> list[DurableTask]:
    """Return the child rows this task should become, or ``[]`` to leave it alone.

    Returns rows rather than writing them: the caller owns persistence, so a
    rejected plan costs nothing and a half-written graph is impossible. ``[]`` is
    always a safe answer — the parent simply stays retryable.

    NEVER raises. A decomposition that fails must leave the task exactly as it was.
    """
    if decomposer is None:
        return []
    goal = (task.goal or "").strip()
    if not goal:
        return []
    try:
        specs = await decomposer.decompose_specs(goal)
    except Exception as exc:
        log.tasks.error(
            "[loop] decomposition failed — the task stays retryable as it was",
            exc_info=exc, extra={"_fields": {"task_id": task.task_id}},
        )
        return []

    if len(specs) < 2:
        # The decomposer's documented fail-safe returns ONE spec that IS the whole
        # objective, so a single-step plan is a decomposition MISS, not a plan.
        # Materialising it would add a row, a hop and a delivery boundary while
        # changing nothing about why the task keeps failing.
        log.tasks.info(
            "[loop] decomposition produced no real split — leaving the task whole",
            extra={"_fields": {"task_id": task.task_id, "specs": len(specs)}},
        )
        return []

    try:
        from stackowl.objectives.graph import validate_graph

        problem = validate_graph(specs)
    except Exception as exc:
        log.tasks.error(
            "[loop] could not validate the decomposition graph — rejecting the plan",
            exc_info=exc, extra={"_fields": {"task_id": task.task_id}},
        )
        return []
    if problem is not None:
        # Rejecting the WHOLE batch, not the offending edge. A cycle means those
        # rows would wait on each other for ever and the parent would wait on
        # both — three stuck rows and nothing to notice them. An ordinary retry is
        # recoverable; a deadlocked graph is not.
        log.tasks.warning(
            "[loop] decomposition REJECTED — the plan is not a valid graph",
            extra={"_fields": {"task_id": task.task_id, "kind": problem.kind,
                               "detail": str(problem.detail)[:160]}},
        )
        return []

    from stackowl.pipeline.durable.task import DurableTask

    ids = [f"{task.task_id}-sub{i}-{uuid.uuid4().hex[:6]}" for i in range(len(specs))]
    children: list[DurableTask] = []
    for i, spec in enumerate(specs):
        # The decomposer emits depends_on as INDICES into its own batch; the graph
        # stores task ids. Getting this translation wrong yields a plan with no
        # edges, which runs every step at once and looks correct until order
        # matters — so it is asserted by test rather than trusted.
        deps = tuple(
            ids[j] for j in (spec.depends_on or []) if 0 <= j < len(ids) and j != i
        )
        children.append(DurableTask(
            task_id=ids[i],
            goal=spec.description,
            status="pending",
            trigger_kind="subgoal",
            parent_task_id=task.task_id,
            depends_on=deps,
            # Inherited so a child can be completed on delivery like anything else —
            # a child that lost its destination could never satisfy the one rule
            # the whole loop turns on.
            destination=task.destination,
            channel=task.channel,
            owl_name=task.owl_name,
            session_key=task.session_key,
            achievement=(
                spec.acceptance_criteria.description
                if getattr(spec, "acceptance_criteria", None) is not None
                else "this step is done and its result is available to the parent"
            ),
            max_attempts=task.max_attempts,
        ))
    log.tasks.info(
        "[loop] task split into sub-tasks — one loop became several",
        extra={"_fields": {"task_id": task.task_id, "children": len(children),
                           "after_attempts": task.attempt_count,
                           "edges": sum(len(c.depends_on) for c in children)}},
    )
    return children
