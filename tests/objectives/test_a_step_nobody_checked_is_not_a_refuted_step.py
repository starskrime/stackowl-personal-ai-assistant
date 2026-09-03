""""We never checked" and "we checked and it was false" are different answers.

MEASURED 2026-09-03 on the live ``objective_subgoals`` table:

    rows                                     28
    carrying an acceptance criterion          4   (14%)
    stored verified value        {0: 26, 1: 2}
    stored NULLs                              0

The column is documented tri-state — ``store._loads_verified``: "NULL ⇒ None
(not evaluated); a stored 1/0 ⇒ True/False" — and the schema is nullable
(``verified INTEGER``), and the INSERT writes NULL, and ``update_subgoal`` skips
the write entirely when handed None. Every layer supports three states. Not one
NULL survives, because the single writer flattens it::

    verified = verdict.accepted is True     # driver.py

``verdict.accepted`` is None when no criterion was declared or derived — the
default, since the LLM deriver is flag-gated off. That None becomes False.

WHAT THAT COSTS HIM, and it is a sentence he reads. ``aggregate_verdicts`` is
built for exactly three states::

    refuted_count = sum(1 for v in verdicts if v is False)
    if refuted_count > 0:
        return AggregateVerdict(False, ..., f"{refuted_count}/{total} step(s) refuted")
    ...
    unconfirmed = total - verified_count - refuted_count
    return AggregateVerdict(None, ..., f"... {unconfirmed} unconfirmed")

Any single False makes the WHOLE objective's verdict "refuted", and the driver
puts that reason straight into the completion notification:

    ✓ Objective complete: <intent>
    (0/28 steps independently verified — 28/28 step(s) refuted)

"Refuted" means reality was observed and contradicted the claim. Nobody looked.
The flattening was written to avoid over-claiming a verified success — its
comment says so — and it avoids that by manufacturing a claim of FAILURE
instead, which is the same overclaim pointed the other way.

AND IT MADE A WHOLE BRANCH UNREACHABLE. ``unconfirmed`` is positive only when
some verdict is None, and no None is ever stored — so the aggregator's
"N verified; K unconfirmed" path, the one written for precisely this situation,
has never executed in production. That is the tell: when a carefully-built
branch is dead, something upstream is collapsing the input it was built for.

THE ROOT CAUSE IS ONE FIELD CARRYING TWO MEANINGS, on the concept this programme
calls its deepest root — "success ASSERTED or GUESSED, never MEASURED". The
verification primitive's own bookkeeping could not tell measured-false from
never-measured.
"""

from __future__ import annotations

import uuid as uuid_module
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.objectives.model import Objective, SubgoalSpec
from stackowl.objectives.store import ObjectiveStore
from stackowl.pipeline.acceptance_authority import aggregate_verdicts

pytestmark = pytest.mark.anyio

#: The live shape: 28 steps, 4 with criteria (2 observed true, 1 refuted, 1 with
#: a null artifact_dir), the rest never evaluated.
LIVE_TOTAL = 28
LIVE_WITH_CRITERIA = 4


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "objectives.db"
    seed_schema(db_path)
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _objective(objective_id: str = "obj-1") -> Objective:
    return Objective(
        objective_id=objective_id, owner_id="principal-default",
        intent="watch X and handle it", channel="telegram",
        target_channels=["telegram"], target_addresses={"telegram": 12345},
    )


# --------------------------------------------------------------------------- #
# The consequence he reads                                                     #
# --------------------------------------------------------------------------- #


def test_unchecked_steps_are_not_reported_as_refuted() -> None:
    """THE DEFECT, in the sentence it produces. Flattened, the live table reads
    as 28 refuted steps — "reality contradicted every one" — when 24 of them
    were simply never looked at."""
    flattened = [False] * (LIVE_TOTAL - 2) + [True, True]
    assert "refuted" in aggregate_verdicts(flattened).reason

    honest = [None] * (LIVE_TOTAL - LIVE_WITH_CRITERIA) + [True, True, False, None]
    verdict = aggregate_verdicts(honest)  # type: ignore[arg-type]
    assert verdict.refuted_count == 1, (
        f"only the ONE observed-and-contradicted step is refuted: {verdict}"
    )


def test_the_unconfirmed_branch_is_reachable_at_all() -> None:
    """A branch that cannot execute is the tell that its input was collapsed
    upstream. ``unconfirmed`` is positive only when some verdict is None."""
    verdict = aggregate_verdicts([True, None, None])  # type: ignore[list-item]
    assert verdict.accepted is None
    assert "unconfirmed" in verdict.reason
    assert verdict.refuted_count == 0


def test_a_genuinely_refuted_step_still_dominates() -> None:
    """The other direction, and the reason this is not "call everything
    unconfirmed": a step whose declared post-condition was observed ABSENT is a
    verified failure and must still sink the objective's verdict."""
    verdict = aggregate_verdicts([True, True, False, None])  # type: ignore[list-item]
    assert verdict.accepted is False
    assert "refuted" in verdict.reason


# --------------------------------------------------------------------------- #
# The storage tri-state, end to end                                            #
# --------------------------------------------------------------------------- #


async def _one_subgoal(pool: DbPool) -> tuple[ObjectiveStore, str, str]:
    store = ObjectiveStore(pool)
    obj = _objective(f"obj-{uuid_module.uuid4().hex[:8]}")
    await store.create(obj)
    subs = await store.add_subgoals(obj.objective_id, [SubgoalSpec(description="do a")])
    return store, obj.objective_id, subs[0].subgoal_id


async def test_a_subgoal_starts_not_evaluated(pool: DbPool) -> None:
    """The INSERT writes NULL, which deserializes to None. This is the state the
    driver was overwriting with False on every clean run."""
    store, oid, sid = await _one_subgoal(pool)
    sub = (await store.list_subgoals(oid))[0]
    assert sub.verified is None, sub.verified


async def test_completing_without_a_criterion_leaves_it_not_evaluated(
    pool: DbPool,
) -> None:
    """THE REGRESSION at the storage layer. ``update_subgoal`` treats None as
    "leave as-is", so passing the tri-state straight through keeps NULL — the
    honest record that nothing was checked."""
    store, oid, sid = await _one_subgoal(pool)
    await store.update_subgoal(sid, "done", result="finished", verified=None)
    sub = (await store.list_subgoals(oid))[0]
    assert sub.status == "done"
    assert sub.verified is None, (
        "a step nobody checked was recorded as verified=False, which the "
        "aggregator reports to him as 'refuted'"
    )


@pytest.mark.parametrize("value", [True, False])
async def test_an_actual_verdict_is_still_stamped(pool: DbPool, value: bool) -> None:
    """Both real verdicts must still persist — the fix must not turn the column
    into a write-only NULL."""
    store, oid, sid = await _one_subgoal(pool)
    await store.update_subgoal(sid, "done", result="x", verified=value)
    sub = (await store.list_subgoals(oid))[0]
    assert sub.verified is value


# --------------------------------------------------------------------------- #
# The writer                                                                   #
# --------------------------------------------------------------------------- #


def test_the_driver_passes_the_tri_state_through() -> None:
    """THE ROOT CAUSE, asserted where it lives. ``verdict.accepted is True``
    reads three states and writes two. Every layer beneath it — model, schema,
    deserializer, store API, aggregator — carries three."""
    import inspect

    from stackowl.objectives import driver

    source = inspect.getsource(driver)
    assert "verified = verdict.accepted is True" not in source, (
        "the driver still flattens 'not evaluated' into 'not verified', which "
        "the aggregator then reports to him as 'refuted'"
    )
    assert "verified = verdict.accepted" in source
