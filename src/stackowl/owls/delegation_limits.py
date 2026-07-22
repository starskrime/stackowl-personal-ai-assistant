"""Delegation safety limits — shared constants for the E8 multi-agent rails.

These bound recursive agent-spawn (fork-bomb) and total concurrent pipeline
load on a single host. They are consumed by:

* ``PipelineState.delegation_depth`` + the child-toolset exclusion (depth cap).
* The ``delegate_task`` tool (S1), which refuses — structured, never raising —
  past ``MAX_DELEGATION_DEPTH`` or ``MAX_CONCURRENT_DELEGATIONS``.
* :class:`stackowl.owls.concurrency.ConcurrencyGovernor`, which caps total
  in-flight delegated + parliament pipelines at ``MAX_INFLIGHT_PIPELINES``.

Centralized here (not scattered) so every enforcement site reads ONE source of
truth (ARCH-90 / placement-voting: a single owned module).
"""

from __future__ import annotations

# Raised 2 -> 6 on 2026-07-22 (owner decision): this was a guess at normal
# usage, not a measured pathology — a genuinely complex multi-part task
# shouldn't be structurally blocked. Still finite; _MAX_ANCESTOR_WALK
# (durable/delegation_link.py) remains the real cycle-protection backstop.
MAX_DELEGATION_DEPTH = 6

# Total concurrent in-flight pipelines (delegated + parliament) on one host.
# The shared ConcurrencyGovernor blocks/awaits a slot past this. This is the
# PHYSICAL host ceiling (this box's hardware) and is deliberately NOT profile-
# specific: an autonomous run draws from the exact same slot pool as an
# interactive one, bounded by the same GOVERNOR_ACQUIRE_TIMEOUT_SECONDS fail-
# fast — widening the caps below never bypasses this hardware rail.
MAX_INFLIGHT_PIPELINES = 4

# Per-turn (per trace_id) fan-out width cap; prevents depth-1 × width-N blow-up.
# The S1 tool refuses past this; provided here for that enforcement site.
# Raised 4 -> 12 on 2026-07-22 (owner decision) — see MAX_DELEGATION_DEPTH.
MAX_CONCURRENT_DELEGATIONS = 12

# Autonomous (unattended — no human watching each level, e.g. an
# ObjectiveDriverHandler-driven epic subgoal) delegation caps. A background
# coding subgoal legitimately needs more logical depth/width than an
# interactive chat turn a human is watching live (e.g. explore -> implement ->
# review), but it still draws from the SAME shared MAX_INFLIGHT_PIPELINES /
# ConcurrencyGovernor host ceiling above — these only widen the TREE SHAPE,
# never the physical concurrency cap, so raising them is safe even on small
# hardware.
# Raised 2026-07-22 (owner decision) — see MAX_DELEGATION_DEPTH above.
MAX_DELEGATION_DEPTH_AUTONOMOUS = 10
MAX_CONCURRENT_DELEGATIONS_AUTONOMOUS = 20


def depth_cap(delegation_profile: str) -> int:
    """The effective depth cap for a delegation profile ("interactive"/"autonomous").

    Unknown/malformed profile values fall back to the stricter interactive cap
    (fail-safe — a widened budget is never granted by default)."""
    if delegation_profile == "autonomous":
        return MAX_DELEGATION_DEPTH_AUTONOMOUS
    return MAX_DELEGATION_DEPTH


def width_cap(delegation_profile: str) -> int:
    """The effective per-turn width cap for a delegation profile. See :func:`depth_cap`."""
    if delegation_profile == "autonomous":
        return MAX_CONCURRENT_DELEGATIONS_AUTONOMOUS
    return MAX_CONCURRENT_DELEGATIONS

# Bounded wait for a governor slot. A delegated child acquires WITH this timeout
# so that — if every permit is held by parents awaiting their own children
# (acquire-while-holding) — a child fails fast (structured) and replies, freeing
# the parent rather than deadlocking forever.
# Raised 45.0 -> 1800.0 on 2026-07-22 (owner decision: relax delegation limits).
# NOT removed outright, unlike most limits in this pass — MAX_INFLIGHT_PIPELINES
# stays a hard host ceiling, so a child that never gets a slot would otherwise
# wait forever (a real deadlock, not just a slow turn). 30 minutes is long
# enough to never fire in normal operation while still being an eventual escape
# hatch, not an infinite wait.
GOVERNOR_ACQUIRE_TIMEOUT_SECONDS = 1800.0

# E8-S3 — idle time-to-live for a named session. A session whose `last_active`
# (monotonic) is older than this is reaped by the SessionRegistry sweep (its A2A
# mailbox drained) so an abandoned session never leaks. 1800s (30m) mirrors the
# clarify-park horizon so the whole platform reaps idle state on one cadence.
SESSION_IDLE_TTL_SECONDS = 1800.0
