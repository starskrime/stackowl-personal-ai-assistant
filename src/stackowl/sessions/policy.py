"""Reset policy and branch resolution — the decision made on every inbound message.

Pure functions over an entry and a clock. No I/O, no store, no side effects, so
every branch of invariant I3 is unit-testable without a database or a gateway.

The priority order IS the behaviour, and it is the thing most likely to regress:

    1. suspended       -> force a new incarnation   (hard wipe wins, always)
    2. resume_pending  -> PRESERVE the incarnation  (soft recovery)
    3. policy expired  -> new incarnation           (daily / idle)
    4. otherwise       -> carry on

A hard wipe must never be overridden by a soft recovery. See
``docs/reference-mapping/designs/D01.7.md``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum

from stackowl.infra.observability import log
from stackowl.sessions.models import Branch, ResetReason, SessionEntry

# Bakir signed off 4 AM over his own initial midnight answer (2026-07-25): a
# session running at 1 AM is mid-thought and midnight guillotines it, whereas at
# 4 AM nobody is talking. Configurable so midnight stays reachable.
DEFAULT_AT_HOUR = 4
DEFAULT_IDLE_MINUTES = 1440  # 24h
# Three consecutive restarts that failed to complete a turn on one lane -> wipe it.
# Per-lane so one poisoned conversation cannot take the others down (Q7).
DEFAULT_MAX_RESTART_FAILURES = 3


class ResetMode(StrEnum):
    """Which automatic boundaries are armed."""

    NONE = "none"
    IDLE = "idle"
    DAILY = "daily"
    BOTH = "both"

    @property
    def daily_armed(self) -> bool:
        return self in (ResetMode.DAILY, ResetMode.BOTH)

    @property
    def idle_armed(self) -> bool:
        return self in (ResetMode.IDLE, ResetMode.BOTH)


@dataclass(frozen=True, slots=True)
class ResetPolicy:
    """When a lane's incarnation ends by itself."""

    mode: ResetMode = ResetMode.BOTH
    at_hour: int = DEFAULT_AT_HOUR
    idle_minutes: int = DEFAULT_IDLE_MINUTES
    notify_on_reset: bool = True
    max_restart_failures: int = DEFAULT_MAX_RESTART_FAILURES

    def __post_init__(self) -> None:
        if not 0 <= self.at_hour <= 23:
            raise ValueError(f"at_hour must be 0-23, got {self.at_hour}")
        if self.idle_minutes < 1:
            raise ValueError(f"idle_minutes must be >= 1, got {self.idle_minutes}")


@dataclass(frozen=True, slots=True)
class Resolution:
    """What the resolver decided, and why. Logged as the DECISION point."""

    branch: Branch
    reason: ResetReason | None = None
    # True when the caller must mint a new session_id for this lane.
    mints_new_incarnation: bool = False


def policy_from_settings(session_settings: object) -> ResetPolicy:
    """Build a :class:`ResetPolicy` from the ``session`` config section.

    Takes the settings object structurally rather than by import, so this module
    stays free of a dependency on ``config`` (which imports far more than a policy
    needs) and remains unit-testable with a plain stub.

    An unrecognised ``reset_mode`` falls back to ``both`` — the shipped default —
    and says so loudly. Failing closed to ``none`` would silently disable every
    boundary, which is the failure nobody notices until a conversation has run for
    a month.
    """
    raw = str(getattr(session_settings, "reset_mode", ResetMode.BOTH.value)).strip().lower()
    try:
        mode = ResetMode(raw)
    except ValueError:
        log.config.error(
            "[sessions] policy_from_settings: unknown reset_mode — using 'both'",
            extra={"_fields": {"configured": raw,
                               "valid": [m.value for m in ResetMode]}},
        )
        mode = ResetMode.BOTH
    return ResetPolicy(
        mode=mode,
        at_hour=int(getattr(session_settings, "at_hour", DEFAULT_AT_HOUR)),
        idle_minutes=int(getattr(session_settings, "idle_minutes", DEFAULT_IDLE_MINUTES)),
        notify_on_reset=bool(getattr(session_settings, "notify_on_reset", True)),
        max_restart_failures=int(
            getattr(session_settings, "max_restart_failures", DEFAULT_MAX_RESTART_FAILURES)
        ),
    )


def expired_reason(entry: SessionEntry, now: datetime.datetime,
                   policy: ResetPolicy) -> ResetReason | None:
    """Which automatic boundary has passed, if any. ``None`` means still current.

    Idle is checked before daily only because it is the cheaper comparison; the two
    are mutually exclusive in effect (either way the lane rolls), so the order does
    not change behaviour — just which reason the user is shown.
    """
    if policy.mode is ResetMode.NONE:
        return None

    if policy.mode.idle_armed:
        deadline = entry.updated_at + datetime.timedelta(minutes=policy.idle_minutes)
        if now > deadline:
            return ResetReason.IDLE

    if policy.mode.daily_armed:
        boundary = now.replace(hour=policy.at_hour, minute=0, second=0, microsecond=0)
        if now.hour < policy.at_hour:
            # Today's boundary has not happened yet, so the relevant one is yesterday's.
            boundary -= datetime.timedelta(days=1)
        if entry.updated_at < boundary:
            return ResetReason.DAILY

    return None


def resolve(entry: SessionEntry | None, now: datetime.datetime, policy: ResetPolicy,
            *, has_active_work: bool = False,
            process_started_at: datetime.datetime | None = None) -> Resolution:
    """Decide what happens to this lane on this message. Exactly one branch (I3).

    ``has_active_work`` carries Bakir's Q12 answer, which EXTENDS the reference platform' rule
    rather than adopting it. Theirs protects a running background process; ours
    additionally protects an in-flight durable task, an active objective, and a
    pending clarify question — because StackOwl has autonomy machinery the reference platform does
    not, and an agent working overnight must not have its conversation cut from
    under it (invariant I4). The caller composes those four into one boolean; this
    module deliberately does not know what "work" means.
    """
    if entry is None:
        return Resolution(Branch.NEW, mints_new_incarnation=True)

    # 1 — hard wipe beats everything, including active work. It is set by an
    #     explicit /stop or by the stuck-loop escape, and both mean "this lane is
    #     unusable"; honouring active work here would keep a broken lane alive.
    if entry.suspended:
        return Resolution(Branch.SUSPENDED, ResetReason.SUSPENDED,
                          mints_new_incarnation=True)

    # 2 — soft recovery PRESERVES the incarnation so the transcript continues.
    #     Checked before policy so a crash during the small hours cannot be turned
    #     into a rollover, which would silently discard the turn we are recovering.
    if entry.resume_pending:
        return Resolution(Branch.RESUME)

    # 3 — automatic boundaries, unless the lane is busy (I4).
    reason = expired_reason(entry, now, policy)
    if reason is not None:
        if has_active_work:
            return Resolution(Branch.EXISTING)
        return Resolution(Branch.EXPIRED, reason, mints_new_incarnation=True)

    # 4 — the process that froze this incarnation's prompt is gone (ESC-13).
    #     Measured 2026-08-15: 8 incarnations carried 2-3 distinct prompt hashes,
    #     every one of them a snapshot re-minted under a session_id that outlived
    #     the process holding it. A restarted core IS a new incarnation, so the id
    #     rolls and the byte-identical invariant becomes true by construction
    #     rather than by hope.
    #
    #     DELIBERATELY AFTER the resume check above. A restart is exactly when
    #     resume_pending is set, and that branch exists to preserve the
    #     incarnation so the turn being recovered is not discarded. Rolling first
    #     would break crash recovery to fix a metric.
    #
    #     ``process_started_at`` is passed in rather than read here so this module
    #     stays pure and the trigger stays testable without a clock.
    #
    #     LAST, after the automatic boundaries. A lane can be BOTH due a daily
    #     rollover and older than this process; reporting that as RESTART would
    #     swallow the user's 'new conversation' notice and the summary that
    #     goes with it. A real conversation boundary outranks a process one.
    if (
        process_started_at is not None
        and entry.created_at < process_started_at
        and not has_active_work
    ):
        return Resolution(Branch.EXPIRED, ResetReason.RESTART,
                          mints_new_incarnation=True)

    return Resolution(Branch.EXISTING)


def should_suspend_for_restart_loop(entry: SessionEntry, policy: ResetPolicy) -> bool:
    """Whether this lane has failed to survive restarts often enough to be wiped."""
    return entry.restart_failures >= policy.max_restart_failures


def reset_notice(entry: SessionEntry) -> str | None:
    """The one-line notice shown once after an automatic reset (I5), else ``None``.

    Returns ``None`` for an explicit /new: the user did that on purpose and must
    never be told their conversation expired. This is why ``is_fresh_reset`` is
    kept distinct from ``was_auto_reset``.
    """
    if not entry.was_auto_reset or entry.auto_reset_reason is None:
        return None
    if not entry.auto_reset_reason.is_automatic:
        return None
    why = {
        ResetReason.DAILY: "the previous one ended overnight",
        ResetReason.IDLE: "the previous one went quiet",
        ResetReason.CONTEXT_FULL: "the previous one got too long",
    }[entry.auto_reset_reason]
    return f"— new conversation ({why}) —"
