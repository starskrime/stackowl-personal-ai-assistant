"""TraceContext carries retry_lineage_id (Workstream B, Phase 1)."""

from __future__ import annotations

from stackowl.infra.trace import TraceContext


def test_get_exposes_retry_lineage_id_when_started() -> None:
    token = TraceContext.start("sess", trace_id="tr", retry_lineage_id="row-7")
    try:
        assert TraceContext.get()["retry_lineage_id"] == "row-7"
    finally:
        TraceContext.reset(token)


def test_retry_lineage_id_is_none_when_absent() -> None:
    token = TraceContext.start("sess", trace_id="tr")
    try:
        assert TraceContext.get()["retry_lineage_id"] is None
    finally:
        TraceContext.reset(token)


def test_reset_restores_prior_retry_lineage_id() -> None:
    outer = TraceContext.start("s", trace_id="t", retry_lineage_id="parent-lineage")
    try:
        inner = TraceContext.start("s", trace_id="t", retry_lineage_id="child-lineage")
        TraceContext.reset(inner)
        assert TraceContext.get()["retry_lineage_id"] == "parent-lineage"
    finally:
        TraceContext.reset(outer)


def test_retry_lineage_id_survives_trace_id_churn() -> None:
    """The whole point of this field: a stable lineage id that outlives the
    per-attempt trace_id RetryActuator mints on each attempt_retry call —
    two separate TraceContext.start() calls for different trace_ids but the
    SAME retry_lineage_id must both expose that same lineage id."""
    attempt_1 = TraceContext.start("s", trace_id="attempt-1-trace", retry_lineage_id="row-42")
    try:
        assert TraceContext.get()["retry_lineage_id"] == "row-42"
        assert TraceContext.get()["trace_id"] == "attempt-1-trace"
    finally:
        TraceContext.reset(attempt_1)

    attempt_2 = TraceContext.start("s", trace_id="attempt-2-trace", retry_lineage_id="row-42")
    try:
        assert TraceContext.get()["retry_lineage_id"] == "row-42"
        assert TraceContext.get()["trace_id"] == "attempt-2-trace"
    finally:
        TraceContext.reset(attempt_2)
