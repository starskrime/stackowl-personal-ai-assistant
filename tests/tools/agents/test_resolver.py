"""Tests for resolve_target (structured resolution) in resolver.py.

Covers: explicit present, explicit missing (target_not_found), role match,
default pick, and no-candidate (unresolved).
"""

from __future__ import annotations

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.tools.agents.resolver import resolve_target


def _reg() -> OwlRegistry:
    r = OwlRegistry.with_default_secretary()
    r.register(
        OwlAgentManifest(
            name="scout",
            role="research",
            system_prompt="p",
            model_tier="fast",
        )
    )
    return r


def test_explicit_missing_is_target_not_found() -> None:
    res = resolve_target(registry=_reg(), to_owl="ghost", role=None, caller="secretary")
    assert res.name is None and res.reason == "target_not_found"


def test_explicit_present_ok() -> None:
    res = resolve_target(registry=_reg(), to_owl="scout", role=None, caller="secretary")
    assert res.name == "scout" and res.reason is None


def test_role_match_ok() -> None:
    res = resolve_target(registry=_reg(), to_owl=None, role="research", caller="secretary")
    assert res.name == "scout" and res.reason is None


def test_default_pick_when_no_explicit() -> None:
    res = resolve_target(registry=_reg(), to_owl=None, role=None, caller="secretary")
    assert res.name == "scout" and res.reason is None


def test_no_candidates_unresolved() -> None:
    # Only secretary registered; caller is secretary → no non-caller candidate
    r = OwlRegistry.with_default_secretary()
    res = resolve_target(registry=r, to_owl=None, role=None, caller="secretary")
    assert res.name is None and res.reason == "unresolved"
