"""A failure alert must say WHICH job failed, and for the how-manieth time.

MEASURED 2026-09-03 against the live notification_log. Of 262 ``job_failed``
notifications carrying only 31 distinct messages, ONE message text was delivered
**176 times across 9 distinct job_ids**. Bakir was told "Scheduled job X is
failing repeatedly" 176 times and could not tell from the message which of the
nine jobs it was about.

THE CAUSE. ``_notify_failure`` composed::

    f"Scheduled job '{job.handler_name}' {disposition} after exhausting retries."

``handler_name`` is the job's TYPE, not its identity. Nine different job rows
running the same handler and hitting the same error produce byte-identical text —
which is also why nine of the thirteen repeated ``job_failed`` messages arrived
under different ``job_id``s and the per-(job_id, channel) frequency cap saw each
one as a first send.

WHY THIS AND NOT SUPPRESSION. ESC-117 asks whether identical alerts should be
held back, and that question is genuinely his: this tree already decided the
opposite in code (``lesson_recurrence.py``: "FAILS TOWARD PAGING ... the cost of
the wrong direction here is one duplicate message; the cost of the other is a
self-healing loop that fails in silence"). Naming the occurrence suppresses
NOTHING, contradicts no recorded decision, and fixes the deeper cause — the alert
carried no identity for the thing that failed. Repetition becomes a counter that
tells him it is getting worse, instead of the same sentence again.

THE COUNTER ALREADY EXISTED. ``Job.failure_count`` is maintained by the scheduler
(``scheduler.py`` increments it on the re-arm path) and ``scheduler.py:700``
already spells the occurrence as ``(job.failure_count or 0) + 1`` for the audit
row. The alert now asks the same expression rather than restating it — one
source. All 138 live jobs read 0 today because every one of them is currently
healthy and the counter resets on success; that is the field working, not an
empty table.
"""

from __future__ import annotations

import re

import pytest

from stackowl.db.pool import DbPool
from tests.scheduler.test_s2_failure_notify_and_missing_handler import (
    _AlwaysFailsHandler,
    _exhaust_retries,
    _job,
    _RecordingDeliverer,
    _sched,
)
from stackowl.scheduler.scheduler_helpers import insert_job

pytestmark = pytest.mark.anyio


async def test_the_alert_names_the_job_not_only_its_handler(tmp_db: DbPool) -> None:
    """THE REGRESSION. 176 sends of one sentence across nine jobs, and the
    reader could not tell which job it was about."""
    deliverer = _RecordingDeliverer()
    sched = _sched(tmp_db, _AlwaysFailsHandler("morning_brief"), deliverer=deliverer)
    job = _job("morning_brief")
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)

    assert deliverer.calls, "a retry-exhausted job must alert"
    message = deliverer.calls[-1]["message"]
    assert job.job_id in message, (
        f"the alert names only the handler, so nine different jobs produce the "
        f"same sentence: {message!r}"
    )


async def test_two_jobs_of_the_same_kind_do_not_produce_the_same_sentence(
    tmp_db: DbPool,
) -> None:
    """The exact measured shape: same handler, same failure, different job rows.

    Byte-identical text is why the per-(job_id, channel) cap could not see these
    as repeats — and why the reader could not tell them apart either."""
    deliverer = _RecordingDeliverer()
    sched = _sched(tmp_db, _AlwaysFailsHandler("morning_brief"), deliverer=deliverer)
    first, second = _job("morning_brief"), _job("morning_brief")
    assert first.job_id != second.job_id
    await insert_job(tmp_db, first)
    await insert_job(tmp_db, second)

    await _exhaust_retries(tmp_db, sched, first.job_id)
    a = deliverer.calls[-1]["message"]
    await _exhaust_retries(tmp_db, sched, second.job_id)
    b = deliverer.calls[-1]["message"]

    assert a != b, f"two different jobs still produce identical alert text: {a!r}"


async def test_the_alert_says_how_many_times_this_job_has_failed(
    tmp_db: DbPool,
) -> None:
    """Repetition becomes a COUNTER, not the same sentence again.

    This is what makes ESC-117 answerable without suppressing anything: the
    second alert is different from the first because it says so."""
    deliverer = _RecordingDeliverer()
    sched = _sched(tmp_db, _AlwaysFailsHandler("morning_brief"), deliverer=deliverer)
    job = _job("morning_brief")
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)
    message = deliverer.calls[-1]["message"]

    assert re.search(r"\b1\b", message), (
        f"the first failure should be identifiable as the first: {message!r}"
    )
    row = await tmp_db.fetch_all(
        "SELECT failure_count FROM jobs WHERE job_id = ?", (job.job_id,)
    )
    assert row and int(row[0]["failure_count"]) >= 1, (
        "the counter the alert reads must actually be persisted"
    )


async def test_the_technical_detail_is_still_carried(tmp_db: DbPool) -> None:
    """Adding identity must not cost the reader the error itself — the whole
    point of the alert."""
    deliverer = _RecordingDeliverer()
    sched = _sched(tmp_db, _AlwaysFailsHandler("morning_brief"), deliverer=deliverer)
    job = _job("morning_brief")
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)

    message = deliverer.calls[-1]["message"]
    assert "morning_brief" in message, "the handler name must survive"
    assert "retries" in message or "failing" in message or "failed" in message


async def test_a_transient_retry_does_not_claim_it_exhausted_its_retries(
    tmp_db: DbPool,
) -> None:
    """Found while rendering the fix: the fixed suffix "after exhausting retries"
    was appended to EVERY alert, including the transient-retry one, producing
    "failed transiently and will retry in 60s (attempt 3) after exhausting
    retries" — which says both that it will retry and that it has run out of
    retries. It also must not carry a "Failure #N" count, because failure_count
    counts EXHAUSTIONS and the transient path never increments it; it carries its
    own "(attempt N)" instead."""
    deliverer = _RecordingDeliverer()
    sched = _sched(tmp_db, _AlwaysFailsHandler("morning_brief"), deliverer=deliverer)
    job = _job("morning_brief")
    await insert_job(tmp_db, job)

    # Driven through the same seam the transient caller uses, with the same
    # disposition wording it passes. _exhaust_retries does NOT reach that branch,
    # and a fixture that cannot produce the shape proves nothing about it.
    await sched._notify_failure(
        job, "HTTPError 502", terminal=False,
        disposition="failed transiently and will retry in 60s (attempt 3)",
    )

    assert deliverer.calls, "the transient seam produced no alert"
    m = deliverer.calls[-1]["message"]
    assert "will retry" in m, m
    assert "exhausting retries" not in m, (
        f"a transient retry claims it exhausted its retries: {m!r}"
    )
    assert "Failure #" not in m, (
        f"a transient retry carries an exhaustion counter it never incremented: {m!r}"
    )
    assert job.job_id in m, "the transient alert must still name its job"


async def test_an_exhaustion_alert_still_says_it_exhausted_its_retries(
    tmp_db: DbPool,
) -> None:
    """The other direction — moving the suffix onto the disposition must not
    lose it where it was correct."""
    deliverer = _RecordingDeliverer()
    sched = _sched(tmp_db, _AlwaysFailsHandler("morning_brief"), deliverer=deliverer)
    job = _job("morning_brief")
    await insert_job(tmp_db, job)

    await _exhaust_retries(tmp_db, sched, job.job_id)

    final = deliverer.calls[-1]["message"]
    assert "re-armed" in final or "exhausting retries" in final, final
    assert "Failure #" in final
