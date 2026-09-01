"""E2-S2/S3 — compute_effective_bounds and child_floor: owl(now) ∩ ceiling (enforcement only)."""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.tool_presets import ROUTER_TOOLS
from stackowl.pipeline.authz_compose import child_floor, compute_effective_bounds
from stackowl.pipeline.state import PipelineState


def _state(**kw: object) -> PipelineState:
    base = dict(trace_id="t", session_key="s", input_text="hi", channel="cli",
                owl_name="o", pipeline_step="")
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _reg(bounds: BoundsSpec | None) -> OwlRegistry:
    r = OwlRegistry()
    r.register(OwlAgentManifest(name="o", role="r", system_prompt="s",
                                model_tier="fast", bounds=bounds))
    return r


def test_owl_only_when_no_envelope() -> None:
    # ROUTER_TOOLS ride along since 2026-08-22 — see
    # test_the_appeal_path_survives_a_narrow_owl for why.
    eff = compute_effective_bounds(_state(), _reg(BoundsSpec(tools=frozenset({"a"}))))
    assert eff.tools == frozenset({"a"}) | ROUTER_TOOLS


def test_ceiling_narrows_owl() -> None:
    """The ceiling still narrows — `b` is gone. What it may not do is close the
    appeal, so the router tools survive the intersection."""
    s = _state(creation_ceiling=BoundsSpec(tools=frozenset({"a"})))
    eff = compute_effective_bounds(s, _reg(BoundsSpec(tools=frozenset({"a", "b"}))))
    assert "b" not in eff.tools
    assert eff.tools == frozenset({"a"}) | ROUTER_TOOLS


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


def test_task_envelope_is_ignored_by_enforcement() -> None:
    # E2-S3 — task_envelope is telemetry/presentation only; enforcement is owl ∩ ceiling.
    s = _state(
        creation_ceiling=None,
        task_envelope=BoundsSpec(tools=frozenset({"a"})),  # would narrow if folded
    )
    eff = compute_effective_bounds(s, _reg(BoundsSpec(tools=frozenset({"a", "b"}))))
    assert eff.tools == frozenset({"a", "b"}) | ROUTER_TOOLS  # NOT narrowed by the envelope


# ---------------------------------------------------------------------------
# The appeal path, 2026-08-22
# ---------------------------------------------------------------------------

def test_the_appeal_path_survives_a_narrow_owl() -> None:
    """AN OWL MUST ALWAYS BE ABLE TO ASK, and it could not.

    `ROUTER_TOOLS` exists so an owl can reach `owl_build` to request authority and
    `owls_list` to name a delegation target. It was honoured in builder.py when an
    owl is CREATED and in owl_build_authz.py when a ceiling is minted — and NOT
    here, at the dispatch seam, which is the only place the refusal happens. An
    owl created before those tools joined the set could never ask for anything
    again.

    MEASURED on the live box 2026-08-21: `mailbutler` refused `owl_build` twice and
    `owls_list` three times, its bounds and ceiling frozen at the same 7 tools since
    2026-08-20, with no successful grant anywhere in the log. That is the defect the
    record already named once — "a ceiling that cannot be APPEALED is not a
    legitimate choice, because the operator's answer becomes unreachable rather
    than merely unsought" — fixed for owl creation and left open for owl execution.
    """
    narrow = BoundsSpec(tools=frozenset({"web_search"}))
    eff = compute_effective_bounds(_state(creation_ceiling=narrow), _reg(narrow))
    assert "owl_build" in eff.tools, "the owl cannot ask for authority"
    assert "owls_list" in eff.tools, "the owl cannot name a delegation target"
    assert "web_search" in eff.tools, "its real grant must survive"


def test_an_EMPTY_allowlist_is_still_absolute() -> None:
    """The other jaw, and an existing invariant lock caught the first version of
    the fix breaking it.

    `test_empty_allowlist_blocks_even_discovery_meta_tools` states that
    `tools=frozenset()` denies everything including the discovery meta-tools, with
    "no auto-exemption". An EMPTY allowlist is an operator saying "this owl may do
    nothing" — complete and deliberate. A NON-EMPTY one is a working list that may
    simply predate ROUTER_TOOLS. Widening the first is privilege escalation;
    widening the second restores a promised appeal.
    """
    eff = compute_effective_bounds(_state(), _reg(BoundsSpec(tools=frozenset())))
    assert eff.tools == frozenset(), (
        f"an explicitly empty allowlist must stay empty, got {sorted(eff.tools)}"
    )


def test_an_unbounded_owl_is_not_NARROWED_into_a_five_tool_allowlist() -> None:
    """The inversion this must never perform: a `None` spec means unrestricted, and
    handing it ROUTER_TOOLS would turn 'everything' into 'these five'."""
    assert compute_effective_bounds(_state(), _reg(None)) is None
