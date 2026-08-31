"""The done-callback I wrote this morning does the opposite of its own comment.

Both shadow spawn sites in ``turn_task.py`` carried::

    # Hold a reference so the task is not garbage-collected mid-flight,
    # and surface a crash instead of swallowing it.
    shadow.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

``Task.exception()`` RETRIEVES the exception. Retrieving it is precisely what
suppresses asyncio's "Task exception was never retrieved" warning — the only place
this failure was ever going to be reported — and the lambda then discards it. So
the callback took the single existing report of a crashed shadow task and silenced
it, while its comment claimed the reverse.

MEASURED 2026-08-31: every OTHER ``add_done_callback`` in ``src/`` passes a named
handler — ``_on_drive_done`` (recovery.py:405, 722), ``_on_liveness_task_done``
(telegram/adapter.py:273), ``_on_story_task_done`` (objectives/driver.py:410) — or
a set-``discard``. The retrieve-and-drop lambda appears at exactly two sites, both
added by me on 2026-08-31, and ``_on_drive_done``'s own docstring states the rule
the lambda breaks: an exception here "would otherwise surface only as an
unretrieved-exception warning", so it is logged.

WHY IT MATTERS BEYOND TIDINESS. These two tasks are the achievement observer and
the achievement judge — the shadow half of the completion contract. If either dies,
the platform simply has no opinion about whether a turn achieved anything, and
nothing anywhere says so. That is the write-with-no-reader shape that Bakir's
phase-5 note names when it asks for a flush barrier: "a background write that fails
silently".

AND THE BARRIER PATTERN ALREADY EXISTS ONE FILE OVER. ``feedback.py`` starts its
background classify with ``create_task`` and carries the handle on
``state.feedback_classify_task``; ``execute.py:3378`` JOINS it. That is a real
flush barrier, already built and already correct — cited here so the difference is
deliberate: the shadow tasks must NOT be joined (the enqueue promises never to
delay the turn), which is exactly why their failure has to be logged instead.
"""

from __future__ import annotations

import asyncio
import inspect
import logging

import pytest

from stackowl.pipeline.durable import turn_task

pytestmark = pytest.mark.asyncio


async def test_a_crashed_shadow_task_is_LOGGED() -> None:
    """The behaviour the old comment claimed and the old code prevented."""
    caplog_records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            caplog_records.append(record)

    handler = _Capture()
    logging.getLogger("stackowl.tasks").addHandler(handler)
    try:
        async def _boom() -> None:
            raise RuntimeError("the shadow judge died")

        task = asyncio.create_task(_boom())
        task.add_done_callback(turn_task._on_shadow_done)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    finally:
        logging.getLogger("stackowl.tasks").removeHandler(handler)

    errors = [r for r in caplog_records if r.levelno >= logging.WARNING]
    assert errors, "a shadow task crashed and nothing said so"
    assert "shadow" in errors[-1].getMessage().lower()


async def test_a_SUCCESSFUL_shadow_task_is_quiet() -> None:
    """A record per successful shadow would put a line on every turn for nothing."""
    caplog_records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            caplog_records.append(record)

    handler = _Capture()
    logging.getLogger("stackowl.tasks").addHandler(handler)
    try:
        async def _fine() -> None:
            return None

        task = asyncio.create_task(_fine())
        task.add_done_callback(turn_task._on_shadow_done)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    finally:
        logging.getLogger("stackowl.tasks").removeHandler(handler)

    assert [r for r in caplog_records if r.levelno >= logging.WARNING] == []


async def test_a_CANCELLED_shadow_task_is_not_an_error() -> None:
    """Shutdown cancels in-flight shadows. That is not a defect and must not read
    like one, or every restart writes a false alarm."""
    caplog_records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            caplog_records.append(record)

    handler = _Capture()
    logging.getLogger("stackowl.tasks").addHandler(handler)
    try:
        async def _slow() -> None:
            await asyncio.sleep(30)

        task = asyncio.create_task(_slow())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        task.add_done_callback(turn_task._on_shadow_done)
        await asyncio.sleep(0)
    finally:
        logging.getLogger("stackowl.tasks").removeHandler(handler)

    assert [r for r in caplog_records if r.levelno >= logging.WARNING] == []


def test_the_swallowing_lambda_is_GONE_from_both_sites() -> None:
    """Structural, over the source, because the defect was a one-line idiom that a
    later reader could reintroduce without noticing what it costs."""
    source = inspect.getsource(turn_task)

    # Targets the CALLBACK, not the prose: `_on_shadow_done`'s docstring quotes the
    # removed idiom on purpose, and a test that cannot tell an explanation from the
    # thing it explains would forbid documenting the defect at all.
    assert "add_done_callback(lambda" not in source, (
        "an inline lambda callback is back — the retrieve-and-discard form "
        "suppresses the only warning a crashed shadow task would ever produce"
    )
    assert source.count("add_done_callback(_on_shadow_done)") == 2, (
        "both shadow spawn sites must use the named handler"
    )


def test_the_reference_is_still_held() -> None:
    """The callback's OTHER job. Without a strong reference the task can be
    garbage-collected mid-flight, which is why the lambda existed at all."""
    source = inspect.getsource(turn_task)
    assert "_SHADOW_TASKS" in source, (
        "dropping the lambda must not drop the reference it was also holding"
    )
