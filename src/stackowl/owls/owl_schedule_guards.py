"""Guardrails for `lifecycle="scheduled"` owls (UniOwl ADR-B, Story S11).

Pure, side-effect-free helpers (mirrors :mod:`owl_build_guards`): an interval
FLOOR so a scheduled owl can never fire faster than the box can afford.

Founder decision (UNIOWL_IMPLEMENTATION_PLAN.md, resolved):
* interval floor **5 min** (sub-5 needs an explicit ≥5-min schedule — we REFUSE
  rather than silently clamp, so the manifest, the single source of truth, can
  never hold a hotter trigger than was approved).

No per-user scheduled-owl quota and no consecutive-failure circuit breaker
(owner decision 2026-07-22) — see ``MIN_SCHEDULED_INTERVAL_SECONDS`` below.
"""

from __future__ import annotations

# `schedule_interval_seconds` LIVES IN `scheduler_helpers` NOW, beside
# `compute_next_run`, and is imported rather than restated. It answers a
# question about the SCHEDULER's schedule language, and the scheduler needs it
# too (`recover` derives a missed job's replay policy from the period). Two
# implementations of one cadence rule is the shape this repo pays for most;
# importing it keeps the name this module has always exported.
from stackowl.scheduler.scheduler_helpers import schedule_interval_seconds

# The provenance marker stamped into a scheduled owl's projected ``jobs`` row
# (``params['source']``). Reconcile only ever touches rows carrying it; a
# hand-made cronjob (``params['created_by']='cronjob'``) never does, so reconcile
# can never delete a user's own cron. Also read by the scheduler's crash-time
# self-heal requeue (``_requeue_circuit_broken``, unrelated to the removed
# per-job consecutive-failure breaker).
OWL_LIFECYCLE_SOURCE = "owl_lifecycle"

# Interval floor: a scheduled owl may not fire more often than this (Jetson-safe).
MIN_SCHEDULED_INTERVAL_SECONDS = 300.0
# No per-user scheduled-owl quota and no consecutive-failure circuit breaker
# (owner decision 2026-07-22) — a failing recurring job keeps re-arming and
# alerting instead of being permanently paused (see scheduler/scheduler.py).


def interval_floor_error(schedule: str) -> str | None:
    """Return a refusal string if ``schedule`` fires faster than the floor, else None.

    Used at manifest validation (so a sub-floor scheduled owl can never be minted)
    and defensively at projection. An unparseable interval is NOT rejected here
    (``None``) — schedule validity is a separate, earlier gate.
    """
    seconds = schedule_interval_seconds(schedule)
    if seconds is not None and seconds < MIN_SCHEDULED_INTERVAL_SECONDS:
        floor_min = int(MIN_SCHEDULED_INTERVAL_SECONDS // 60)
        return (
            f"scheduled owls may not run faster than every {floor_min} minutes "
            f"(requested {schedule!r} ≈ every {int(seconds)}s) — use a slower schedule."
        )
    return None


