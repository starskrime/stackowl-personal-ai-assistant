"""An agent that just created a scheduled owl must be able to confirm it.

BAKIR, 2026-08-22: "Platform does not have capability to create agent without any
issue." The creation worked. The CONFIRMATION did not, and that is what the agent
reported.

MEASURED, on a create that fully succeeded. At 04:00:35 `owl_build` created
`syshealth`; at 04:00:47 it was granted read_logs/shell/write_file; its row landed
as job `owl_lifecycle-syshealth`, handler goal_execution, schedule "every 1h",
status pending, enabled=1. Then at 04:00:50:

    cronjob failed: "no such job: 'syshealth'"

The agent had asked about its own new owl BY NAME, been told it did not exist, and
concluded the task had failed — reporting "The capability that failed: owl_build"
about two owl_build calls that both returned success.

`owl_build` mints the id via `scheduler.owl_lifecycle._job_id_for` and `cronjob`
demanded a different spelling of the same entity: two tools disagreeing about one
identity, which is the "two copies of one rule" shape. Resolved by asking the
minter rather than restating its prefix.
"""

from __future__ import annotations

from stackowl.scheduler.job import Job
from stackowl.scheduler.owl_lifecycle import _job_id_for
from stackowl.tools.scheduling.cron_helpers import (
    CREATED_BY_TAG,
    count_owl_jobs,
    filter_owl_jobs,
    find_owned_job,
)


def _lifecycle_job(owl: str) -> Job:
    return Job(
        job_id=_job_id_for(owl),
        handler_name="goal_execution",
        schedule="every 1h",
        idempotency_key=f"k-{owl}",
        last_run_at=None,
        next_run_at="2026-08-22T05:00:00+00:00",
        status="pending",
        params={"owl": owl},
    )


def _tool_job(owl: str) -> Job:
    """A job the cron TOOL created — the only kind ownership used to recognise."""
    return Job(
        job_id=f"cron-{owl}-1",
        handler_name="goal_execution",
        schedule="every 2h",
        idempotency_key=f"t-{owl}",
        last_run_at=None,
        next_run_at="2026-08-22T06:00:00+00:00",
        status="pending",
        params={"owl": owl, "created_by": CREATED_BY_TAG, "goal": "x"},
    )


def test_the_owl_NAME_resolves_to_its_lifecycle_job() -> None:
    """THE FIX. `syshealth` must find `owl_lifecycle-syshealth`."""
    jobs = [_lifecycle_job("syshealth")]
    found = find_owned_job(jobs, "syshealth", "syshealth")
    assert found is not None, (
        "an agent cannot confirm the owl it just created — which is what made a "
        "successful create report as a failure"
    )
    assert found.job_id == "owl_lifecycle-syshealth"


def test_the_full_job_id_still_resolves() -> None:
    """The exact id must keep working — the alias is additive, not a replacement."""
    jobs = [_lifecycle_job("syshealth")]
    found = find_owned_job(jobs, "owl_lifecycle-syshealth", "syshealth")
    assert found is not None
    assert found.job_id == "owl_lifecycle-syshealth"


def test_a_name_that_matches_NOTHING_still_resolves_to_nothing() -> None:
    """The alias must not invent a job. A caller asking about something that does
    not exist still gets None."""
    jobs = [_lifecycle_job("syshealth")]
    assert find_owned_job(jobs, "ghost", "syshealth") is None


def test_the_alias_does_NOT_bypass_the_OWNERSHIP_gate() -> None:
    """THE SECURITY HALF, and the reason this is an alias rather than a relaxed
    match. `find_owned_job` is deliberately not an existence oracle: a missing job
    and another owl's job must be indistinguishable, or a caller can probe for jobs
    it does not own. The alias resolves a SPELLING, never a permission.
    """
    jobs = [_lifecycle_job("syshealth")]
    # A different owl asking by name must get the same answer as for a missing job.
    assert find_owned_job(jobs, "syshealth", "someone_else") is None
    assert find_owned_job(jobs, "owl_lifecycle-syshealth", "someone_else") is None


def test_what_an_owl_can_LIST_is_what_it_is_COUNTED_for() -> None:
    """The two must never disagree — that disagreement was the defect.

    `count_owl_jobs` carried its own inline copy of the ownership predicate. When
    `_owns` learned that an owl's lifecycle job is its own, this did not, so a
    scheduled owl would list three jobs and be told it had two. Nothing crashes;
    the soft-cap nudge simply lies, and a budget that disagrees with the listing
    is worse than no budget at all.

    Pinned as an INVARIANT between the two functions rather than as a hardcoded
    number, so it keeps holding whichever way ownership evolves next.
    """
    jobs = [
        _lifecycle_job("syshealth"),
        _tool_job("syshealth"),
        _lifecycle_job("Brain"),
        _tool_job("Brain"),
    ]
    for owl in ("syshealth", "Brain"):
        assert count_owl_jobs(jobs, owl) == len(filter_owl_jobs(jobs, owl)), (
            f"{owl} can list {len(filter_owl_jobs(jobs, owl))} jobs but is counted "
            f"for {count_owl_jobs(jobs, owl)} — the soft cap contradicts the listing"
        )
