"""The retry sweep is deleted, and its row cannot come back.

WHY A TEST AND NOT JUST A DELETE. Migration 0125 removed a job row while its
seeder still lived, and the scheduler re-seeded it thirty-one seconds later, on
every boot — CLAUDE.md lists that as failure shape 6, "deleting a row while its
writer lives". This retirement removes the row (migration 0134) AND the handler
module AND its registration, and this test is what keeps all three gone
together.

WHAT WAS DELETED AND WHY, measured 2026-09-03. Commit 49601f50 removed the only
writer of ``retry_queue`` on 2026-08-28 — "a floored turn retries on the ONE
loop" — and left the sweep scheduled at ``every 1m``:

    retry_queue newest row      2026-08-28T03:31:27
    retry_queue pending rows    0
    retry_sweep.execute exits   989 / 888 / 902 / 934 / 946 on the five days
                                after the writer went, every one returning nothing

1,440 dispatches a day, for six days, against a table that can never gain a row.

WHAT DELIBERATELY STAYS: ``RetryActuator``. ``task_loop_runner`` builds a
``RetryQueueRow`` from a ``tasks`` row and calls ``attempt_retry`` — that IS
"retries on the ONE loop", and it ran 13-21 times a day throughout. A test that
asserted the whole retry vocabulary was gone would be asserting the opposite of
what the platform needs.
"""

from __future__ import annotations

import importlib
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


@pytest.mark.tripwire
def test_the_handler_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("stackowl.scheduler.handlers.retry_sweep")


@pytest.mark.tripwire
def test_nothing_registers_or_seeds_it() -> None:
    """The writer, not just the row. A seeder left behind re-creates the job on
    the next boot and the deletion silently undoes itself."""
    offenders = []
    for path in (_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in ("register_retry_sweep_handler", 'handler_name="retry_sweep"',
                       "handler_name='retry_sweep'"):
            if needle in text:
                offenders.append(f"{path.relative_to(_ROOT)}: {needle}")
    assert not offenders, (
        "something still registers or seeds the retry sweep — the job row will "
        "come back on the next boot:\n  " + "\n  ".join(offenders)
    )


def test_the_ACTUATOR_is_still_here() -> None:
    """The control, and it matters more than the two above. The actuator is what
    "retries on the ONE loop" means; deleting it while deleting the sweep would
    take the platform's retry path with it."""
    mod = importlib.import_module("stackowl.pipeline.retry_actuator")
    assert hasattr(mod, "RetryActuator")
    assert hasattr(mod.RetryActuator, "attempt_retry")
