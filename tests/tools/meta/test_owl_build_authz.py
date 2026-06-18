"""owl_build authority core — security math unit tests (no consent, no I/O).

The clamp is a NO-OP for an unbounded creator (floor=None → intersection returns
the request verbatim), so an unbounded creator gets SAFE_DEFAULT_CEILING and
consequential tools (shell/exec/write/network) are dropped here and must be
explicitly widened by a human at consent.
"""

from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.tools.meta.owl_build_authz import (
    SAFE_DEFAULT_CEILING,
    build_agent_manifest,
    clamp_bounds,
    resolve_creation_ceiling,
)
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec


def _reg_with(name: str, bounds: BoundsSpec | None) -> OwlRegistry:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name=name, role=name, system_prompt="p", model_tier="fast", bounds=bounds
        ),
        source_name="t",
    )
    return reg


def test_safe_default_ceiling_is_read_only_ish() -> None:
    tools = SAFE_DEFAULT_CEILING.tools or frozenset()
    assert "shell" not in tools and "execute_code" not in tools and "write_file" not in tools
    assert "read_file" in tools  # research/read allowed


def test_unbounded_creator_gets_safe_default_ceiling() -> None:
    reg = _reg_with("secretary", None)  # unbounded
    ceiling = resolve_creation_ceiling("secretary", None, reg)
    assert ceiling == SAFE_DEFAULT_CEILING


def test_bounded_creator_ceiling_is_its_floor() -> None:
    reg = _reg_with("narrow", BoundsSpec(tools=frozenset({"read_file"})))
    ceiling = resolve_creation_ceiling("narrow", None, reg)
    assert ceiling.tools == frozenset({"read_file"})


def test_clamp_drops_tools_above_ceiling() -> None:
    requested = BoundsSpec(tools=frozenset({"read_file", "shell"}))
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    clamped, dropped = clamp_bounds(requested, ceiling)
    assert clamped.tools == frozenset({"read_file"})
    assert dropped == frozenset({"shell"})


def test_build_agent_manifest_forces_authority_and_clamps() -> None:
    reg = _reg_with("secretary", None)
    spec = OwlBuildSpec(action="create", name="scout", preset="researcher", specialty="recon")
    manifest, dropped = build_agent_manifest(
        spec, creator="secretary", parent_ceiling=None, registry=reg
    )
    assert manifest.origin == "agent"
    assert manifest.created_by == "secretary"
    assert manifest.creation_ceiling == SAFE_DEFAULT_CEILING
    assert manifest.name == "scout"
    assert "shell" not in (manifest.bounds.tools or frozenset())


def test_build_with_explicit_shell_drops_it_under_unbounded_creator() -> None:
    reg = _reg_with("secretary", None)
    spec = OwlBuildSpec(
        action="create", name="coder", explicit_tools=["read_file", "shell"], specialty="builds"
    )
    manifest, dropped = build_agent_manifest(
        spec, creator="secretary", parent_ceiling=None, registry=reg
    )
    assert "shell" not in (manifest.bounds.tools or frozenset())  # dropped by safe-default ceiling
    assert "shell" in dropped
