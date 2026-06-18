"""Process-substrate safety limits — shared constants for the E9 OS-process rails.

These bound an UNGATED ``process.start`` (it inherits the shell's max-autonomy
philosophy: any command runs, only a narrow catastrophic set needs consent). The
real rails here are NOT a prompt — they are a hard concurrency cap plus a
MANDATORY per-process maximum lifetime so an ungated spawn can neither fork-bomb
nor leak a runaway process forever:

* ``MAX_CONCURRENT_PROCESSES`` — the count cap the registry's ``start`` refuses
  past (structured, never raising).
* ``PROCESS_MAX_LIFETIME_SECONDS`` — the mandatory TTL: every handle gets a
  ``ttl_deadline`` and the sweep AUTO-KILLS a process that outlives it.
* ``PER_STREAM_BUFFER_BYTES`` / ``AGGREGATE_BUFFER_BYTES`` — bound captured
  stdout/stderr so a chatty process cannot exhaust memory; the aggregate ceiling
  evicts the oldest process's buffer first.

Centralised here (mirrors :mod:`stackowl.owls.delegation_limits`) so every
enforcement site reads ONE source of truth (ARCH-90 / placement-voting).
"""

from __future__ import annotations

# Hard cap on concurrently-live tracked processes. A personal assistant rarely
# needs more than a handful of parallel background processes; past this the
# ``ProcessRegistry.start`` refuses (structured, never raising) so an ungated
# caller cannot fork-bomb the host.
MAX_CONCURRENT_PROCESSES = 8

# Per-stream rolling capture ceiling (stdout AND stderr each get one). Older
# bytes are dropped past this; the buffer records how many were dropped so the
# truncation is honest, never silent.
PER_STREAM_BUFFER_BYTES = 200 * 1024  # 200 KiB

# Aggregate capture ceiling across ALL live processes' buffers. When exceeded,
# the registry evicts the OLDEST process's captured buffer first (the process
# keeps running; only its already-captured bytes are released).
AGGREGATE_BUFFER_BYTES = 4 * 1024 * 1024  # 4 MiB

# MANDATORY maximum lifetime for any process. Every handle's ``ttl_deadline`` is
# set to ``now + this`` at spawn; the sweep auto-kills a process still running
# past its deadline so an ungated spawn can never leak a runaway forever.
PROCESS_MAX_LIFETIME_SECONDS = 3600.0  # 1h

# A dead (exited/killed/failed) handle is retained this long after it terminated
# so the agent can still poll its final output, then the sweep prunes it.
DEAD_HANDLE_PRUNE_SECONDS = 1800.0  # 30m

# Cadence of the recurring sweep (TTL auto-kill + dead-handle prune + aggregate
# buffer enforcement). A fraction of both TTLs so neither is overshot by ~2×.
SWEEP_INTERVAL_SECONDS = 600.0  # 10m

# Bounds the blocking ``wait`` tool (S2). A requested wait is clamped to this so
# a single wait can never wedge a turn indefinitely.
WAIT_MAX_TIMEOUT_SECONDS = 300.0  # 5m

# Poll cadence inside the blocking ``wait`` loop (S2).
WAIT_POLL_INTERVAL_SECONDS = 0.5
