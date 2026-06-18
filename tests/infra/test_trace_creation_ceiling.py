"""E2-S2 — TraceContext.creation_ceiling: stamped, reset, excluded from get()."""

from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.infra.trace import TraceContext


def test_creation_ceiling_stamped_and_readable() -> None:
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    token = TraceContext.start("sess", trace_id="t1", creation_ceiling=ceiling)
    try:
        assert TraceContext.creation_ceiling() == ceiling
    finally:
        TraceContext.reset(token)


def test_creation_ceiling_reset_to_none_after_token() -> None:
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    token = TraceContext.start("sess", trace_id="t2", creation_ceiling=ceiling)
    TraceContext.reset(token)
    assert TraceContext.creation_ceiling() is None


def test_creation_ceiling_defaults_to_none_when_not_stamped() -> None:
    token = TraceContext.start("sess", trace_id="t3")
    try:
        assert TraceContext.creation_ceiling() is None
    finally:
        TraceContext.reset(token)


def test_get_dict_does_not_include_bounds_spec() -> None:
    """BoundsSpec objects must never appear in log records (get() dict)."""
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    token = TraceContext.start("sess", trace_id="t4", creation_ceiling=ceiling)
    try:
        ctx = TraceContext.get()
        # No BoundsSpec in any value — log records must stay JSON-serializable primitives.
        for value in ctx.values():
            assert not isinstance(value, BoundsSpec), (
                f"BoundsSpec leaked into get() under key with value {value!r}"
            )
        # creation_ceiling key must not be present at all.
        assert "creation_ceiling" not in ctx
    finally:
        TraceContext.reset(token)
