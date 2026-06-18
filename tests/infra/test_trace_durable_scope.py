"""TraceContext carries durable scope (task_id / durable_owner_id) — Story D1 §8.1."""

from __future__ import annotations

from stackowl.infra.trace import TraceContext


def test_get_exposes_task_id_when_started() -> None:
    token = TraceContext.start(
        "sess", trace_id="tr", task_id="child-7", durable_owner_id="owner-a",
    )
    try:
        assert TraceContext.get()["task_id"] == "child-7"
        assert TraceContext.durable_owner_id() == "owner-a"
    finally:
        TraceContext.reset(token)


def test_task_id_is_none_when_absent() -> None:
    token = TraceContext.start("sess", trace_id="tr")
    try:
        assert TraceContext.get()["task_id"] is None
        assert TraceContext.durable_owner_id() is None
    finally:
        TraceContext.reset(token)


def test_reset_restores_prior_durable_scope() -> None:
    outer = TraceContext.start("s", trace_id="t", task_id="parent", durable_owner_id="o")
    try:
        inner = TraceContext.start("s", trace_id="t", task_id="child", durable_owner_id="o")
        TraceContext.reset(inner)
        assert TraceContext.get()["task_id"] == "parent"
    finally:
        TraceContext.reset(outer)
