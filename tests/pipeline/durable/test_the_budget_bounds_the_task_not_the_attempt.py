"""A budget that forgets on every retry is not a budget.

WHY THIS EXISTS. `BudgetGovernor` carries two cumulative meters, cost and input
tokens, and they are supposed to bound the same thing: the DURABLE TASK, across
every attempt it takes. Cost did. Tokens did not, and the difference was one
identifier.

MEASURED 2026-09-06, in `execute.py`, twenty-five lines apart:

    _prior_cost_usd    = await _cost_store.get_accumulated_cost(state.task_id)
    _totals            = await _services.cost_tracker.get_turn_token_totals(state.trace_id)

The token seed GATES on `state.task_id` and then LOOKS UP by `state.trace_id`.
`retry_actuator.py:225` and `goal_execution.py:194` mint `f"retry-{uuid4}"` and
`f"goal-{uuid4}"` per attempt, so the lookup found nothing every time, the seed was
0, and each attempt was handed the whole `max_input_tokens` allowance again.

What that cost, from `cost_records` on the live box: on 2026-09-02 between 14:00 and
15:30 the goal `goal-goal_execution-7b6da65e` ran NINE traces, 162 model calls and
**4,172,113 input tokens — 66% of that entire day's 6,346,818** — and never
completed. Seven of the nine each exceeded the 500,000 cap meant to bound the whole
task. Across the database, 362M input tokens have run through `goal-`/`retry-`/
`recover-` traces, which are precisely the prefixes minted per attempt.

The governor's own docstring says this was fixed, citing a 3,893,308-token incident
"under ONE trace_id". It was fixed only for the shape that REUSES a trace id. On the
shape that mints a new one it recurred at 86% of its original size, after the fix —
which is the falsifier for "self-healing proves the root-cause fix".

So the invariant is not "the seed is non-zero". It is that **both meters answer to
the same key**, and that key is the durable task. A string check would pass the
moment someone wrote `task_id` in a comment; this reads the call itself.
"""

from __future__ import annotations

import ast
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask

_EXECUTE = (
    Path(__file__).resolve().parents[3]
    / "src" / "stackowl" / "pipeline" / "steps" / "execute.py"
)

#: The two cumulative seeds, by the method each one calls.
_SEEDS = ("get_accumulated_cost", "get_accumulated_input_tokens")


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "tokens.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _task(task_id: str) -> DurableTask:
    now = datetime.now(tz=UTC)
    return DurableTask(
        task_id=task_id, owner_id="principal-default", goal="g",
        status="running", owl_name="o", channel="cli",
        created_at=now, updated_at=now,
    )


def _seed_arguments() -> dict[str, list[str]]:
    """For each cumulative seed, the attribute names it is called with.

    Read from the AST rather than by grepping: the whole defect was one call
    passing a different attribute of the same object, which no string search over
    the file distinguishes from the correct one sitting 25 lines above it.
    """
    tree = ast.parse(_EXECUTE.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {name: [] for name in _SEEDS}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in found:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Attribute):
                found[node.func.attr].append(arg.attr)
    return found


class TestBothMetersAnswerToTheSameKey:
    @pytest.mark.tripwire
    def test_the_token_seed_is_keyed_on_the_durable_task(self) -> None:
        """THE DEFECT ITSELF. `trace_id` here is a fresh uuid on every retry."""
        args = _seed_arguments()

        assert args["get_accumulated_input_tokens"], (
            "no cumulative token seed is called at all — the token half of the "
            "budget has no memory across attempts"
        )
        assert "trace_id" not in args["get_accumulated_input_tokens"], (
            "the token budget is seeded from the ATTEMPT's trace id, which "
            "retry_actuator and goal_execution mint fresh per attempt — every "
            "retry gets the full allowance again"
        )
        assert "task_id" in args["get_accumulated_input_tokens"]

    @pytest.mark.tripwire
    def test_cost_and_tokens_use_the_SAME_key(self) -> None:
        """One rule, one key. Cost was right and tokens were wrong for weeks
        precisely because nothing asked whether the two agreed."""
        args = _seed_arguments()

        assert set(args["get_accumulated_cost"]) == set(
            args["get_accumulated_input_tokens"]
        ), (
            "the two cumulative meters are seeded from different identifiers: "
            f"cost={args['get_accumulated_cost']}, "
            f"tokens={args['get_accumulated_input_tokens']}"
        )

    def test_the_reader_sees_a_real_population(self) -> None:
        """VACUITY CONTROL. If the AST walk found neither call, both assertions
        above would pass over empty lists."""
        args = _seed_arguments()

        assert args["get_accumulated_cost"], "the cost seed vanished — walk is broken"


class TestTheColumnRemembersAcrossAttempts:
    async def test_a_new_task_starts_at_zero(self, pool: DbPool) -> None:
        store = DurableTaskStore(pool, "principal-default")
        await store.create(_task("t-1"))
        assert await store.get_accumulated_input_tokens("t-1") == 0

    async def test_a_second_attempt_inherits_the_first_attempts_tokens(
        self, pool: DbPool
    ) -> None:
        """The assertion whose absence let this ship: the SAME task, read back
        with no reference to whatever trace id the next attempt happens to mint."""
        store = DurableTaskStore(pool, "principal-default")
        await store.create(_task("t-2"))
        await store.set_accumulated_input_tokens("t-2", 480_000)

        assert await store.get_accumulated_input_tokens("t-2") == 480_000

        # Absolute cumulative total, never an additive delta — idempotent on replay.
        await store.set_accumulated_input_tokens("t-2", 495_000)
        assert await store.get_accumulated_input_tokens("t-2") == 495_000

    async def test_a_missing_task_reads_zero_not_error(self, pool: DbPool) -> None:
        store = DurableTaskStore(pool, "principal-default")
        assert await store.get_accumulated_input_tokens("nope") == 0
