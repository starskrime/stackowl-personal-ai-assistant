"""The last of the four engines is gone, and cannot come back by accident.

CLAUDE.md records that this tree accumulated FOUR overlapping engines for "work to
do": `tasks` (live), `retry_queue`, `objectives`/`objective_subgoals`, and
`job_queue`. Bakir's rule of 2026-08-17 is that there is ONE loop. His own commit
49601f50 removed retry_queue's only writer on 2026-08-28 and left the rest
standing — the store, the sweep, the table, the classifier, the telegram backfill.

MEASURED 2026-09-03 before the drop: 5,766 rows, newest 2026-08-28T03:31:27, ZERO
pending, and 7 of the store's 8 methods with no live caller at all. The eighth
updated a PENDING row in a table that could never gain one.

WHAT SURVIVED IS THE POINT OF THE TEST. `RetryActuator` is load-bearing — the ONE
loop re-drives recovered tasks through it — and a sweep of "delete everything
named retry" would have taken it. So this asserts the engine is gone AND the
mechanism is not.
"""

from __future__ import annotations

import importlib
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


@pytest.mark.tripwire
def test_the_store_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("stackowl.memory.retry_queue_store")


@pytest.mark.tripwire
def test_no_code_constructs_or_wires_the_store() -> None:
    """The writer, not just the rows. A construction left behind re-creates the
    dependency and the next reader restores the table to satisfy it."""
    # CODE CONSTRUCTS, NOT THE STRING. A first cut flagged any line containing the
    # name and fired on five DOCSTRINGS that record why the thing is gone — which
    # is the record this programme runs on, not a reference. Prose naming a retired
    # module is how the next reader learns it was retired; an import is how they
    # conclude it is alive.
    needles = (
        "from stackowl.memory.retry_queue_store",
        "import retry_queue_store",
        "RetryQueueStore(",
        "retry_queue_store=",
        ".retry_queue_store",
    )
    offenders = []
    for path in (_ROOT / "src").rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if any(n in line for n in needles):
                offenders.append(f"{path.relative_to(_ROOT)}:{i}: {line.strip()[:70]}")
    assert not offenders, "live code still reaches the retired store:\n  " + "\n  ".join(offenders)


def test_the_ACTUATOR_and_its_row_shape_SURVIVED() -> None:
    """The control, and it matters more than the two above: the actuator is what
    "retries on the ONE loop" means. A retirement that took it would have deleted
    the platform's retry path while looking tidy."""
    actuator = importlib.import_module("stackowl.pipeline.retry_actuator")
    attempt = importlib.import_module("stackowl.pipeline.retry_attempt")

    assert hasattr(actuator, "RetryActuator")
    assert hasattr(actuator.RetryActuator, "attempt_retry")
    assert hasattr(attempt, "RetryAttempt")


def test_the_row_shape_still_carries_what_the_loop_LEARNED() -> None:
    """`banned_capabilities` is the learning failed attempts paid for. It is the
    field most likely to be dropped by a move, and dropping it sends the next
    attempt back down a route already proven dead — task 8b7c4029 failed 74 times
    that way."""
    from stackowl.pipeline.retry_attempt import RetryAttempt

    row = RetryAttempt(
        id="t", trace_id="t", session_key="s", goal="g",
        banned_capabilities=["web_fetch"], attempt_count=4,
    )
    assert row.banned_capabilities == ["web_fetch"]
    assert row.attempt_count == 4
