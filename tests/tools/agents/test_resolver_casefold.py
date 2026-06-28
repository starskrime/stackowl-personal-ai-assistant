"""resolve_target case-fold + display_name awareness (ADR-D / S8).

Delegation must resolve a spoken/cased name to the same owl the gateway routes
to. Exact-slug behaviour is preserved byte-for-byte; case-folded and
display_name matches are additive; an ambiguous token never guesses.
"""

from __future__ import annotations

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.tools.agents.resolver import resolve_target


def _owl(name: str, display: str = "") -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        display_name=display,
        role="specialist",
        system_prompt="help",
        model_tier="standard",
    )


def _reg(*owls: OwlAgentManifest) -> OwlRegistry:
    reg = OwlRegistry()
    for o in owls:
        reg.register(o)
    return reg


def test_exact_slug_still_resolves() -> None:
    reg = _reg(_owl("tony", "Tony"))
    r = resolve_target(registry=reg, to_owl="tony", role=None, caller="boss")
    assert r.name == "tony"
    assert r.reason is None


def test_case_folded_slug_resolves() -> None:
    reg = _reg(_owl("tony", "Tony"))
    r = resolve_target(registry=reg, to_owl="TONY", role=None, caller="boss")
    assert r.name == "tony"
    assert r.reason is None


def test_display_name_resolves_to_slug() -> None:
    # Spoken display "Tony" maps to a different routing slug.
    reg = _reg(_owl("t_stark", "Tony"))
    r = resolve_target(registry=reg, to_owl="tony", role=None, caller="boss")
    assert r.name == "t_stark"
    assert r.reason is None


def test_unknown_name_is_target_not_found() -> None:
    reg = _reg(_owl("tony", "Tony"))
    r = resolve_target(registry=reg, to_owl="ghost", role=None, caller="boss")
    assert r.name is None
    assert r.reason == "target_not_found"


def test_ambiguous_display_name_does_not_guess() -> None:
    # Two owls share the same display token → never silently pick one.
    reg = _reg(_owl("a_owl", "Tony"), _owl("b_owl", "Tony"))
    r = resolve_target(registry=reg, to_owl="Tony", role=None, caller="boss")
    assert r.name is None
    assert r.reason == "target_not_found"
