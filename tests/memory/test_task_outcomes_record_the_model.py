"""ESC-47/50 — task_outcomes must record WHICH MODEL ran the turn.

BAKIR'S DECISION, 2026-08-24: "Add the column, start recording." No behaviour
change to the tool cap today — the point is that the data exists before anything
can be relative to it.

WHY IT WAS BLOCKING. The capability-relative tool cap needs to know what a turn
was run by. `task_outcomes` carries 26 columns and 17,673 rows and not one of
them says which model produced the row, so every comparison across models is
unanswerable retroactively. There has only ever been one model here, which is
exactly why this is cheap to add now and expensive to add later: the column can
start filling before there is anything to compare.

WHERE IT IS STAMPED, AND WHY THERE. `select_tool_provider_plan` is the single
choke point for resolving which provider and model run a turn — three callers,
one of them internal. Stamping inside it means every real turn is covered by one
edit rather than each call site remembering.

The QUIET PROBE IS EXCLUDED. `assemble` calls the same selector with
`log_selection=False` purely to size the context window, "side-effect-free" by
its own comment. If that stamped too, a cached-prompt turn would record the
probe's answer rather than the selection that actually ran, and the column would
quietly describe the plan instead of the effect.

THE CARRIER IS A ContextVar, which is the established idiom here and not a new
engine: `record()` ALREADY reads `lesson_experiment.current_arm()` the same way,
with the comment "classify decides the arm, and this recorder is several hops
away". The model has the identical shape — chosen in execute, needed in a
recorder several hops later, and unavailable on the streaming path as a return
value because a stream yields text, not a CompletionResult.
"""

from __future__ import annotations

import pytest

from stackowl.infra import turn_model


@pytest.fixture(autouse=True)
def _clean() -> object:
    """Never leak a stamp between tests — a ContextVar default is per-context."""
    token = turn_model.set_model("")
    yield
    turn_model.reset(token)


# ---------------------------------------------------------------------------
# The carrier
# ---------------------------------------------------------------------------

def test_the_default_is_empty_not_a_guess() -> None:
    """An unknown model must read as unknown. Inventing a name here would put a
    false value in the column that a later comparison would trust."""
    assert turn_model.current_model() == ""


def test_a_stamp_is_readable_back() -> None:
    turn_model.set_model("neraai-v1-raw")
    assert turn_model.current_model() == "neraai-v1-raw"


def test_reset_restores_the_previous_value() -> None:
    token = turn_model.set_model("model-a")
    assert turn_model.current_model() == "model-a"
    turn_model.reset(token)
    assert turn_model.current_model() == ""


def test_a_blank_stamp_does_not_overwrite_a_real_one() -> None:
    """The quiet window probe resolves an empty model when no provider is
    configured. It must not erase the selection that actually ran."""
    turn_model.set_model("neraai-v1-raw")
    turn_model.set_model("")
    assert turn_model.current_model() == "neraai-v1-raw"


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_column_exists_and_is_written(tmp_db: object) -> None:
    """End to end against a real migrated database."""
    from stackowl.memory.outcome_store import TaskOutcomeStore

    cols = {
        r["name"] for r in await tmp_db.fetch_all("PRAGMA table_info(task_outcomes)")  # type: ignore[attr-defined]
    }
    assert "model" in cols, "migration 0122 must add the column"

    turn_model.set_model("neraai-v1-raw")
    await TaskOutcomeStore(tmp_db).record(  # type: ignore[arg-type]
        trace_id="t-model-1", session_key="s", owl_name="secretary",
        channel="cli", success=True, latency_ms=1.0, tool_call_count=0,
        failure_class=None, step_durations={}, input_text="hi", response_text="yo",
    )

    row = (await tmp_db.fetch_all(  # type: ignore[attr-defined]
        "SELECT model FROM task_outcomes WHERE trace_id = ?", ("t-model-1",)
    ))[0]
    assert row["model"] == "neraai-v1-raw", (
        "the recorder must persist the model the turn actually ran on"
    )


@pytest.mark.asyncio
async def test_an_unknown_model_is_NULL_not_a_placeholder(tmp_db: object) -> None:
    """A row that cannot say which model ran must say NOTHING, not "unknown" —
    a placeholder string is a value later analysis would group on."""
    from stackowl.memory.outcome_store import TaskOutcomeStore

    await TaskOutcomeStore(tmp_db).record(  # type: ignore[arg-type]
        trace_id="t-model-2", session_key="s", owl_name="secretary",
        channel="cli", success=True, latency_ms=1.0, tool_call_count=0,
        failure_class=None, step_durations={}, input_text="hi", response_text="yo",
    )

    row = (await tmp_db.fetch_all(  # type: ignore[attr-defined]
        "SELECT model FROM task_outcomes WHERE trace_id = ?", ("t-model-2",)
    ))[0]
    assert row["model"] is None


@pytest.mark.asyncio
async def test_existing_rows_are_untouched_by_the_migration(tmp_db: object) -> None:
    """17,673 rows predate the column. They must survive with NULL, never be
    backfilled with a guess — nobody recorded what ran them."""
    from stackowl.memory.outcome_store import TaskOutcomeStore

    await TaskOutcomeStore(tmp_db).record(  # type: ignore[arg-type]
        trace_id="t-old", session_key="s", owl_name="secretary",
        channel="cli", success=True, latency_ms=1.0, tool_call_count=0,
        failure_class=None, step_durations={}, input_text="old", response_text="row",
    )
    rows = await tmp_db.fetch_all(  # type: ignore[attr-defined]
        "SELECT model FROM task_outcomes WHERE trace_id = ?", ("t-old",)
    )
    assert rows and rows[0]["model"] is None
