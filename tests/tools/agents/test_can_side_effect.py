"""Tests for _can_side_effect — bounds x severity conservative helper.

Tool severity reality (confirmed by recon):
  - read_file: inherits default action_severity="read" (no manifest override)
  - edit:      explicit action_severity="write"
  - shell:     explicit action_severity="write" (it genuinely mutates the world)
"""

from __future__ import annotations

from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, set_services, reset_services
from stackowl.tools.registry import ToolRegistry
from stackowl.tools.agents.delegate_task import _can_side_effect


def _env(owl_bounds: BoundsSpec | None) -> StepServices:
    reg = OwlRegistry()
    reg.register(
        OwlAgentManifest(
            name="coder",
            role="r",
            system_prompt="p",
            model_tier="fast",
            bounds=owl_bounds,
        ),
        source_name="t",
    )
    return StepServices(owl_registry=reg, tool_registry=ToolRegistry.with_defaults())


def test_read_only_child_cannot_side_effect() -> None:
    """An owl restricted to read_file (action_severity=read) cannot side-effect."""
    tok = set_services(_env(BoundsSpec(tools=frozenset({"read_file"}))))
    try:
        assert _can_side_effect("coder") is False
    finally:
        reset_services(tok)


def test_write_capable_child_can_side_effect() -> None:
    """An owl with edit in its bounds (action_severity=write) can side-effect."""
    tok = set_services(_env(BoundsSpec(tools=frozenset({"edit"}))))
    try:
        assert _can_side_effect("coder") is True
    finally:
        reset_services(tok)


def test_unrestricted_bounds_is_conservatively_side_effecting() -> None:
    """An owl with no bounds (None = unrestricted) is conservatively side-effecting."""
    tok = set_services(_env(None))
    try:
        assert _can_side_effect("coder") is True
    finally:
        reset_services(tok)


def test_unknown_owl_or_no_registry_is_conservative() -> None:
    """Unknown owl or missing registry is conservatively side-effecting."""
    tok = set_services(StepServices())
    try:
        assert _can_side_effect("ghost") is True
    finally:
        reset_services(tok)
