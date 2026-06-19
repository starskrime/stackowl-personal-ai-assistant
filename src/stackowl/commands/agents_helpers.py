"""Helpers for the unified `AgentCommand` (Story 7.2).

Kept separate so the command file stays under the 300-line B2 cap and so
the formatting helpers are unit-testable in isolation.
"""

from __future__ import annotations

from typing import Any

from stackowl.scheduler.job import Job

_NO_JOBS_MSG = "(no background agents registered)"
_NO_RUNS_MSG = "No runs recorded for agent {job_id}"


def _truncate(value: str | None, width: int) -> str:
    if value is None:
        return "-"
    return value if len(value) <= width else value[: max(0, width - 1)] + "…"


def format_jobs_table(jobs: list[Job]) -> str:
    """Render a fixed-width table of registered jobs for ``/agent list``."""
    if not jobs:
        return _NO_JOBS_MSG
    header = (
        f"{'id':<10}  {'handler':<18}  {'schedule':<16}  "
        f"{'status':<10}  {'last_run':<25}  {'next_run':<25}  fail"
    )
    rows = [header, "-" * len(header)]
    for job in jobs:
        rows.append(
            f"{job.job_id[:8]:<10}  "
            f"{_truncate(job.handler_name, 18):<18}  "
            f"{_truncate(job.schedule, 16):<16}  "
            f"{_truncate(job.status, 10):<10}  "
            f"{_truncate(job.last_run_at, 25):<25}  "
            f"{_truncate(job.next_run_at, 25):<25}  "
            f"{job.failure_count}"
        )
    return "\n".join(rows)


def format_results_table(job_id: str, rows: list[dict[str, Any]]) -> str:
    """Render the last-N runs of a single agent for ``/agent log <job_id>``."""
    if not rows:
        return _NO_RUNS_MSG.format(job_id=job_id)
    header = (
        f"{'run_at':<25}  {'status':<10}  {'duration_ms':>11}  result"
    )
    out = [header, "-" * (len(header) + 20)]
    for row in rows:
        result_text = row.get("result_text") or ""
        summary = result_text.replace("\n", " ").strip()
        if len(summary) > 100:
            summary = summary[:99] + "…"
        duration = row.get("duration_ms")
        duration_str = f"{duration:.1f}" if isinstance(duration, (int, float)) else "-"
        out.append(
            f"{_truncate(row.get('run_at'), 25):<25}  "
            f"{_truncate(row.get('status'), 10):<10}  "
            f"{duration_str:>11}  "
            f"{summary}"
        )
    return "\n".join(out)
