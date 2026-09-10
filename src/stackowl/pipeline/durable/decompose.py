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


def should_decompose(task: DurableTask, *, prior_failures: int = 0) -> bool:
    """Is splitting this task the right next move?

    False in every doubtful case. A wrong "no" costs one more ordinary retry; a
    wrong "yes" spends an LLM call and multiplies the row into a fan-out that has
    to be managed, so the asymmetry justifies the conservatism.
    """
    # COUNT THE GOAL, NOT ONLY THE ROW. MEASURED 2026-08-31: the Daily Gmail digest
    # failed on `budget:stop:tokens` three times in twenty minutes for 1.75M input
    # tokens, and this gate never opened — because those were three SEPARATE tasks.
    # `task_runner.run` mints `task-{uuid4().hex[:12]}` on every call and the
    # scheduler's job-level retry calls it again, so `attempt_count` resets to 1
    # each time and can never reach 3. Across every retained log there is not one
    # `task RESHAPED` record: the whole decomposition path was correct and
    # structurally unreachable for the case it exists for.
    #
    # `prior_failures` is how many EARLIER rows for this same goal already failed
    # for a reason repetition cannot fix. Defaulted to 0, so every existing caller
    # behaves exactly as before — and passed EXPLICITLY rather than stamped on the
    # task, because a decomposition gate has no business mutating the row it judges.
    failures = task.attempt_count + max(0, int(prior_failures))
    if failures < DECOMPOSE_AFTER_ATTEMPTS:
        return False
    if task.depends_on:
        # Already split. Splitting again would fan out without bound: each child
        # fails, splits, and its children split in turn.
        return False
    # Depth ceiling. A sub-task is meant to BE the simple half; letting it
    # decompose is how a two-level plan becomes a runaway tree.
    return not task.parent_task_id


#: THE CUMULATIVE BUDGET COLUMNS A CHILD DOES **NOT** INHERIT, each with the
#: reason — because until 2026-09-10 nothing anywhere stated that they were reset
#: at all (DEBT-294).
#:
#: WHY THEY WERE INVISIBLE. `accumulated_input_tokens` and `accumulated_cost_usd`
#: are COLUMNS ON THE `tasks` TABLE WITH NO FIELD ON :class:`DurableTask`. They are
#: reachable only through four store methods, so every constructor in this tree —
#: including the child list below, which enumerates its inherited fields one by
#: one — cannot see them. They were not forgotten; they were never visible to be
#: forgotten, and a new row simply takes the column's ``DEFAULT 0``.
#:
#: WHAT THAT COSTS, MEASURED 2026-09-10. `task-89b6c47ac995` was seeded at
#: **508,225** accumulated input tokens — it had breached the 500k cap, and that
#: breach is the `budget` failure class that makes `wants_reshaping` true in the
#: first place. Its NINE children each show `accumulated_input_tokens = 0`, with
#: attempt counts 12, 12, 4, 3, 2, 1, 1, 1; two of those lineages burned 117.9 and
#: 84.9 minutes. **The mechanism that exists because a task overspent resets the
#: meter that measured the overspend.** Over the two days to 2026-09-10 `jobmarket`
#: took 483.8 minutes — 8.06 hours, 82% of ALL turn time across every owl — and 71%
#: of its turns were retries.
#:
#: WHY THEY ARE NOT SIMPLY INHERITED, which is the half a reader will ask about.
#: The parent breached the cap BEFORE deciding to split, so a child inheriting
#: 508,225 against a 500,000 cap is dead on arrival and decomposition becomes a
#: no-op. Inheriting is as wrong as resetting; what a split ask should be allowed
#: to spend in total is a product decision and is queued as ESC-168. What ships
#: here is that the reset is STATED, MEASURED at the moment it happens, and cannot
#: silently acquire a third member.
_NOT_INHERITED_BY_A_CHILD: dict[str, str] = {
    "accumulated_input_tokens": (
        "reset to 0 — inheriting the parent's breaching total would kill every "
        "child immediately; see ESC-168"
    ),
    "accumulated_cost_usd": (
        "reset to 0.0 — same reasoning as the token meter it is persisted beside"
    ),
}


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
