"""Guardrails for `lifecycle="scheduled"` owls (UniOwl ADR-B, Story S11).

Pure, side-effect-free helpers (mirrors :mod:`owl_build_guards`): an interval
FLOOR so a scheduled owl can never fire faster than the box can afford, a per-user
QUOTA on how many scheduled owls may project a standing job, and the shared
constants the projection loop and the scheduler circuit-breaker key on.

Founder decisions (UNIOWL_IMPLEMENTATION_PLAN.md, resolved):
* interval floor **5 min** (sub-5 needs an explicit ≥5-min schedule — we REFUSE
  rather than silently clamp, so the manifest, the single source of truth, can
  never hold a hotter trigger than was approved).
* per-user quota **5 scheduled owls** (reuses the ``MAX_AGENT_OWLS`` soft-cap shape).
* circuit-break after **3 consecutive failed scheduled runs** → pause + ONE alert.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.scheduler.scheduler_helpers import parse_every
from stackowl.tools.scheduling.cron_helpers import parse_daily_hhmm

if TYPE_CHECKING:  # pragma: no cover — typing only (registry imports manifest; avoid cycle)
    from stackowl.owls.registry import OwlRegistry

# The provenance marker stamped into a scheduled owl's projected ``jobs`` row
# (``params['source']``). Reconcile only ever touches rows carrying it; a
# hand-made cronjob (``params['created_by']='cronjob'``) never does, so reconcile
# can never delete a user's own cron. Also read by the scheduler circuit-breaker.
OWL_LIFECYCLE_SOURCE = "owl_lifecycle"

# Interval floor: a scheduled owl may not fire more often than this (Jetson-safe).
MIN_SCHEDULED_INTERVAL_SECONDS = 300.0
# Per-user quota: at most this many scheduled owls project a standing job.
MAX_SCHEDULED_OWLS = 5
# Circuit-breaker: consecutive failed scheduled runs before the job is paused.
MAX_CONSECUTIVE_FAILURES = 3


def schedule_interval_seconds(schedule: str) -> float | None:
    """Best-effort effective interval of a schedule expression, in seconds.

    Handles every accepted scheduler form: ``daily@HH:MM`` (one day), the
    ``every <n><unit>`` token (via the shared :func:`parse_every`), and a 5-field
    cron (the delta between its next two firings). Returns ``None`` when the
    expression is unparseable — the caller then declines to judge it (fail-open;
    an unparseable schedule is rejected earlier by ``is_valid_schedule``).
    """
    text = schedule.strip()
    if text.lower().startswith("daily@"):
        return 86400.0 if parse_daily_hhmm(text) is not None else None
    every = parse_every(text)
    if every is not None:
        return every.total_seconds()
    try:
        from croniter import croniter  # type: ignore[import-untyped]

        base = datetime.now(UTC)
        it = croniter(text, base)
        first: datetime = it.get_next(datetime)
        second: datetime = it.get_next(datetime)
        return (second - first).total_seconds()
    except Exception as exc:  # B5 — never raise out of a pure guard
        log.scheduler.warning(
            "[owls] schedule_interval_seconds: unparseable schedule",
            exc_info=exc,
            extra={"_fields": {"schedule": text}},
        )
        return None


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


def count_scheduled_owls(registry: OwlRegistry) -> int:
    """Number of ``lifecycle='scheduled'`` owls currently registered (quota input)."""
    return sum(1 for m in registry.all() if m.lifecycle == "scheduled")


def scheduled_quota_error(registry: OwlRegistry) -> str | None:
    """Return a refusal string if the scheduled-owl quota is already full, else None.

    Mirrors the ``MAX_AGENT_OWLS`` hard-gate shape: a caller about to mint a NEW
    scheduled owl checks this first. (The projection loop applies the same cap
    defensively so a YAML-edited surplus still cannot flood the box.)
    """
    current = count_scheduled_owls(registry)
    if current >= MAX_SCHEDULED_OWLS:
        return (
            f"you already have {current} scheduled owls (cap {MAX_SCHEDULED_OWLS}) — "
            "retire one before scheduling another."
        )
    return None
