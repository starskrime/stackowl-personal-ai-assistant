"""E2-S2 — compute_effective_bounds: owl(now) ∩ ceiling ∩ envelope, fail-closed."""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.authz_compose import compute_effective_bounds
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
