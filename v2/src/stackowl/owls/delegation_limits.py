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

# Secretary → specialist → sub-specialist; deeper recursion is almost never a
# legitimate shape for a personal assistant. The S1 tool refuses at this depth.
MAX_DELEGATION_DEPTH = 2

# Total concurrent in-flight pipelines (delegated + parliament) on one host.
# The shared ConcurrencyGovernor blocks/awaits a slot past this.
MAX_INFLIGHT_PIPELINES = 4

# Per-turn (per trace_id) fan-out width cap; prevents depth-1 × width-N blow-up.
# The S1 tool refuses past this; provided here for that enforcement site.
MAX_CONCURRENT_DELEGATIONS = 4

# Bounded wait for a governor slot. A delegated child acquires WITH this timeout
# so that — if every permit is held by parents awaiting their own children
# (acquire-while-holding) — a child fails fast (structured) and replies, freeing
# the parent rather than deadlocking forever. Kept below a typical delegation
# receive timeout so the child surrenders before the parent gives up.
GOVERNOR_ACQUIRE_TIMEOUT_SECONDS = 45.0
