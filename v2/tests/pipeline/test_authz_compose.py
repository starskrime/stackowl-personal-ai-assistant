"""E2-S2 — compute_effective_bounds and child_floor: owl(now) ∩ ceiling ∩ envelope."""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.authz_compose import child_floor, compute_effective_bounds
from stackowl.pipeline.state import PipelineState


def _state(**kw: object) -> PipelineState:
    base = dict(trace_id="t", session_id="s", input_text="hi", channel="cli",
                owl_name="o", pipeline_step="")
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _reg(bounds: BoundsSpec | None) -> OwlRegistry:
    r = OwlRegistry()
    r.register(OwlAgentManifest(name="o", role="r", system_prompt="s",
                                model_tier="fast", bounds=bounds))
    return r


def test_owl_only_when_no_envelope() -> None:
    eff = compute_effective_bounds(_state(), _reg(BoundsSpec(tools=frozenset({"a"}))))
    assert eff.tools == frozenset({"a"})


def test_ceiling_narrows_owl() -> None:
    s = _state(creation_ceiling=BoundsSpec(tools=frozenset({"a"})))
    eff = compute_effective_bounds(s, _reg(BoundsSpec(tools=frozenset({"a", "b"}))))
    assert eff.tools == frozenset({"a"})


def test_unbounded_owl_no_envelope_is_none() -> None:
    assert compute_effective_bounds(_state(), _reg(None)) is None


def test_no_registry_is_none() -> None:
    assert compute_effective_bounds(_state(), None) is None


def test_unknown_owl_is_none() -> None:
    assert compute_effective_bounds(_state(owl_name="ghost"), _reg(None)) is None


def test_bounded_owl_compute_error_raises_for_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = _reg(BoundsSpec(tools=frozenset({"a"})))

    def boom(name: str):  # noqa: ANN202
        raise RuntimeError("registry fault")

    monkeypatch.setattr(reg, "get", boom)
    with pytest.raises(RuntimeError):
        compute_effective_bounds(_state(), reg)


# ---------------------------------------------------------------- child_floor


def _reg_parent(parent_bounds: BoundsSpec | None) -> OwlRegistry:
    r = OwlRegistry()
    r.register(OwlAgentManifest(name="parent", role="r", system_prompt="s",
                                model_tier="fast", bounds=parent_bounds))
    return r


def test_child_floor_toctou_case() -> None:
    """TOCTOU gap: parent owl is WIDE {a,b} but ceiling is NARROW {a}.
    child_floor must return {a} (the narrow ceiling wins, not the wide owl bounds)."""
    wide_owl_bounds = BoundsSpec(tools=frozenset({"a", "b"}))
    narrow_ceiling = BoundsSpec(tools=frozenset({"a"}))
    reg = _reg_parent(wide_owl_bounds)
    result = child_floor("parent", narrow_ceiling, reg)
    assert result is not None
    assert result.tools == frozenset({"a"})


def test_child_floor_none_ceiling_back_compat() -> None:
    """No parent ceiling → child_floor equals resolve_owl_bounds (prior behavior)."""
    from stackowl.pipeline.authz_compose import resolve_owl_bounds
    parent_bounds = BoundsSpec(tools=frozenset({"a", "b"}))
    reg = _reg_parent(parent_bounds)
    assert child_floor("parent", None, reg) == resolve_owl_bounds("parent", reg)


def test_child_floor_unknown_owl_with_ceiling_returns_ceiling() -> None:
    """Unknown parent owl (None bounds) ∩ ceiling → ceiling (None ∩ ceiling = ceiling)."""
    ceiling = BoundsSpec(tools=frozenset({"a"}))
    reg = OwlRegistry()  # "parent" not registered
    result = child_floor("parent", ceiling, reg)
    assert result == ceiling
