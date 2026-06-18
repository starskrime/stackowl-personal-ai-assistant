"""SEC-7 / F025 — TraceContext.snapshot() returns the FULL reconstruction set.

``get()`` stays LOG-SAFE (omits the BoundsSpec creation_ceiling + durable_owner_id);
``snapshot()`` is the complete context for reconstruction at a delegation seam,
including ``durable_owner_id`` and ``creation_ceiling``.
"""

from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.infra.trace import TraceContext


def test_get_stays_log_safe() -> None:
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    token = TraceContext.start(
        "sess", trace_id="tr", durable_owner_id="owner-a", creation_ceiling=ceiling,
    )
    try:
        got = TraceContext.get()
        # Log-safe: neither the BoundsSpec nor the owner id appears in get().
        assert "creation_ceiling" not in got
        assert "durable_owner_id" not in got
    finally:
        TraceContext.reset(token)


def test_snapshot_includes_full_reconstruction_set() -> None:
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    token = TraceContext.start(
        "sess",
        trace_id="tr",
        task_id="child-7",
        durable_owner_id="owner-a",
        creation_ceiling=ceiling,
        delegation_depth=2,
    )
    try:
        snap = TraceContext.snapshot()
        # Everything get() has...
        assert snap["trace_id"] == "tr"
        assert snap["task_id"] == "child-7"
        assert snap["delegation_depth"] == 2
        # ...PLUS the full-reconstruction fields get() deliberately omits.
        assert snap["durable_owner_id"] == "owner-a"
        assert snap["creation_ceiling"] is ceiling
    finally:
        TraceContext.reset(token)


def test_snapshot_defaults_when_unset() -> None:
    token = TraceContext.start("sess", trace_id="tr")
    try:
        snap = TraceContext.snapshot()
        assert snap["durable_owner_id"] is None
        assert snap["creation_ceiling"] is None
    finally:
        TraceContext.reset(token)
