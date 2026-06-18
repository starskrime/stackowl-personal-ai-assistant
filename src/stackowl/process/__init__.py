"""Process substrate (E9-S0) — supervised OS-process lifecycle.

The :class:`ProcessRegistry` is the DI singleton owning the full lifecycle of
agent-spawned background OS processes: bounded concurrency + a mandatory maximum
lifetime, captured stdout/stderr, session-scoped queries, and its OWN on-disk
checkpoint + boot reconcile (the Supervisor is NOT used to host processes — a
process exiting is the normal terminal state, never a fault to retry).

The ``process`` and ``wait`` TOOLS that surface this to the agent are S1/S2.
"""

from __future__ import annotations

from stackowl.process.handle import ProcessHandle, ProcessStatus
from stackowl.process.registry import ProcessRegistry, ProcessRegistryError

__all__ = [
    "ProcessHandle",
    "ProcessRegistry",
    "ProcessRegistryError",
    "ProcessStatus",
]
