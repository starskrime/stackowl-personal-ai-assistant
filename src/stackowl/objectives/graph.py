"""Pure dependency-graph functions for epic execution (Task #4).

No DB, no I/O — every function here takes plain in-memory data and returns
plain in-memory data, so the driver's readiness scan and the epic-creation
validation step are both independently unit-testable without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from stackowl.objectives.model import Subgoal, SubgoalSpec

__all__ = ["GraphError", "readiness_set", "validate_graph"]


@dataclass(frozen=True)
class GraphError:
    """Why an epic's decomposed dependency graph is invalid."""

    kind: Literal["cycle", "out_of_range"]
    detail: str


def readiness_set(subgoals: list[Subgoal]) -> set[str]:
    """Return the subgoal_ids of every `pending` story whose dependencies are
    all `done`. A story with no `depends_on` is ready immediately (matches
    every pre-epic row, which has an empty list by default)."""
    done_ids = {sg.subgoal_id for sg in subgoals if sg.status == "done"}
    return {
        sg.subgoal_id
        for sg in subgoals
        if sg.status == "pending" and all(dep in done_ids for dep in sg.depends_on)
    }


def validate_graph(specs: Sequence[SubgoalSpec]) -> GraphError | None:
    """Validate a decomposition batch's `depends_on` indices BEFORE any
    subgoal is persisted (Creation flow §4 of the design spec).

    Uses on-stack marking (three-color DFS) — NOT a flat visited set, which
    would false-reject a legitimate diamond dependency (a node reached twice
    via two different, valid paths is fine; a node reached while still on the
    current recursion stack is a real cycle). Returns the FIRST problem found
    (cycle checked before out-of-range on a given node, deterministic order:
    node 0, 1, 2... by index)."""
    n = len(specs)
    for i, spec in enumerate(specs):
        for dep in spec.depends_on:
            if dep < 0 or dep >= n:
                return GraphError(
                    "out_of_range",
                    f"story {i} depends on index {dep}, but the batch has {n} stories",
                )

    WHITE, GRAY, BLACK = 0, 1, 2
    color = [WHITE] * n

    def visit(i: int, path: list[int]) -> GraphError | None:
        color[i] = GRAY
        for dep in specs[i].depends_on:
            if color[dep] == GRAY:
                cycle = " -> ".join(str(x) for x in (*path, dep))
                return GraphError("cycle", f"dependency cycle: {cycle}")
            if color[dep] == WHITE:
                err = visit(dep, [*path, dep])
                if err is not None:
                    return err
        color[i] = BLACK
        return None

    for i in range(n):
        if color[i] == WHITE:
            err = visit(i, [i])
            if err is not None:
                return err
    return None
