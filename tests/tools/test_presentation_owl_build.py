"""Test that `owl_build` is guaranteed in the base tool set.

Ensures the owl_build tool (self-extension for owls) sits at the same tier as
tool_build in the non-evictable base set — every owl can author a new owl to
overcome a capability gap, just as every owl can author a new tool.
"""

from __future__ import annotations

from stackowl.tools._infra.presentation import _DEFAULT_BASE


def test_owl_build_in_default_base() -> None:
    """owl_build must be in the guaranteed base set (same tier as tool_build)."""
    assert "owl_build" in _DEFAULT_BASE
    assert "tool_build" in _DEFAULT_BASE
