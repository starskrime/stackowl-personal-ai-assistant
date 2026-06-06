"""The fallback delegation must NEVER widen the tool axis beyond the original attempt's
effective bounds (Murat P0-1). The ladder reuses the SAME parent_state => SAME child_floor,
so effective(fallback) = secretary ∩ child_floor ⊆ child_floor = effective(attempt).
A narrow specialist falling back to the secretary therefore gets a NARROW secretary —
no privilege laundering.
"""
import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.authz.bounds_guard import effective_bounds
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.authz_compose import child_floor, resolve_owl_bounds


@pytest.mark.parametrize("narrow_tools", [
    frozenset({"read_file"}),
    frozenset({"web_fetch", "delegate_task"}),
    frozenset(),                                   # deny-all narrow owl
    frozenset({"read_file", "memory", "web_search"}),
])
def test_fallback_floor_is_subset_of_attempt_floor(narrow_tools: frozenset[str]) -> None:
    """When a narrow specialist falls back to the secretary, the secretary runs under the
    SAME child_floor (built from the same parent_state). Its effective tool-axis is
    therefore a subset of the floor — no privilege escalation is possible."""
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(
        name="narrow",
        role="r",
        system_prompt="p",
        model_tier="fast",
        bounds=BoundsSpec(tools=narrow_tools),
    ))

    # The floor BOTH the original attempt AND the fallback use (built once from the caller).
    # child_floor("narrow", None, reg) == effective_bounds(narrow.bounds, None) == narrow.bounds
    floor = child_floor("narrow", None, reg)

    # The secretary's own bounds (unbounded by default — tools=None).
    sec_bounds = resolve_owl_bounds(reg.secretary_name(), reg)

    # What the fallback secretary actually runs under: secretary ∩ floor.
    sec_effective = effective_bounds(sec_bounds, floor)

    # The security property: if the floor restricts tools, the secretary's effective
    # tool-axis must be a SUBSET — it cannot add tools the floor does not permit.
    if floor is not None and floor.tools is not None:
        assert sec_effective is not None, (
            "Secretary effective bounds must not be None when floor restricts tools"
        )
        assert sec_effective.tools is not None, (
            "Secretary effective tools must not be None when floor restricts tools"
        )
        assert sec_effective.tools <= floor.tools, (
            f"SECURITY VIOLATION: secretary gained tools {sec_effective.tools - floor.tools!r} "
            f"not permitted by floor {floor.tools!r}"
        )


def test_unrestricted_floor_stays_unrestricted() -> None:
    """A broad caller (secretary) produces a broad/None floor — the fallback is equally
    broad (forward-flow usefulness). No narrowing of the secretary by itself."""
    reg = OwlRegistry.with_default_secretary()

    # The secretary's own floor (effective bounds of the secretary as a parent).
    # Secretary bounds=None (unbounded), so child_floor returns None.
    floor = child_floor(reg.secretary_name(), None, reg)

    # Secretary clamped to its own floor => still unrestricted.
    sec_effective = effective_bounds(resolve_owl_bounds(reg.secretary_name(), reg), floor)

    # If the secretary is unbounded (tools None), effective stays unbounded — useful in
    # forward flow. The relationship must hold regardless.
    assert sec_effective is None or sec_effective.tools is None or (
        floor is not None
        and floor.tools is not None
        and sec_effective.tools <= floor.tools
    )


def test_narrow_floor_caps_even_after_intersect_with_wider_bounds() -> None:
    """Composing a None-bounded secretary with a narrow floor NEVER widens beyond the floor.

    This is the algebraic core of the no-escalation guarantee:
    effective_bounds(None, narrow_floor) == narrow_floor  (None skipped, single term = identity)
    """
    narrow_tools = frozenset({"read_file", "memory"})
    narrow_floor = BoundsSpec(tools=narrow_tools)

    # Secretary is unbounded (None bounds).
    sec_effective = effective_bounds(None, narrow_floor)

    assert sec_effective is not None
    assert sec_effective.tools == narrow_tools, (
        f"effective_bounds(None, narrow_floor) must equal floor; got {sec_effective.tools!r}"
    )
