"""TS4/ADR-T1 — runtime capability manifest (from reachability) + charter split.

Asserts the manifest is derived from LIVE wiring (reachable, not registered),
omits unbound capabilities, names no tool, is injected into the assembled system
prompt, and that the charter carries the epistemic-honesty (no-invented-"can't")
principle.
"""

from __future__ import annotations

import pytest

from stackowl.owls.base_prompt import behavioral_charter, behavioral_charter_lean
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.capability_manifest import CapabilityManifest
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState

# Tool names that must NEVER leak into the capability manifest (capabilities only,
# per the charter rule). Covers the subsystems the manifest reports on.
_TOOL_NAMES = [
    "web_search",
    "web_fetch",
    "send_message",
    "send_file",
    "proactive_deliverer",
    "cronjob",
    "heartbeat",
    "telegram",
    "scheduler",
    "shell",
]


class _FakeToolRegistry:
    """Minimal stand-in — probe() only calls `.get(name)`."""

    def __init__(self, *, has_shell: bool) -> None:
        self._has_shell = has_shell

    def get(self, name: str) -> object | None:
        return object() if (name == "shell" and self._has_shell) else None


def _services(
    *,
    proactive: bool,
    web: bool,
    reg: OwlRegistry | None = None,
    has_shell: bool = False,
) -> StepServices:
    # Any non-None object is a reachable wiring signal — probe() checks `is not None`.
    sentinel = object()
    return StepServices(
        proactive_deliverer=sentinel if proactive else None,  # type: ignore[arg-type]
        web_search_registry=sentinel if web else None,  # type: ignore[arg-type]
        owl_registry=reg,
        tool_registry=_FakeToolRegistry(has_shell=has_shell),  # type: ignore[arg-type]
    )


def test_manifest_includes_proactive_line_when_reachable() -> None:
    """(a) proactive deliverer reachable → a proactive/scheduling capability line."""
    block = CapabilityManifest.probe(_services(proactive=True, web=False)).render()
    low = block.lower()
    assert "schedule" in low and "proactive" in low
    assert "without" in low and "prompt" in low  # "without waiting to be prompted"


def test_unbound_capability_line_omitted() -> None:
    """(b) an unbound/unreachable capability → its line is OMITTED."""
    # web bound, proactive unbound: only the web line appears.
    block = CapabilityManifest.probe(_services(proactive=False, web=True)).render()
    low = block.lower()
    assert "web" in low
    assert "proactive" not in low and "schedule" not in low


def test_manifest_empty_when_nothing_reachable() -> None:
    """Byte-absent: no capability reachable → empty string (prompt unchanged)."""
    assert CapabilityManifest.probe(_services(proactive=False, web=False)).render() == ""


def test_manifest_has_no_tool_names() -> None:
    """(c) the manifest is capabilities-only — no tool names."""
    block = CapabilityManifest.probe(
        _services(proactive=True, web=True, has_shell=True)
    ).render().lower()
    for name in _TOOL_NAMES:
        assert name not in block, f"manifest must not name tool {name!r}"


def test_manifest_includes_system_exec_line_when_shell_present_and_tools_on() -> None:
    """Local incident (2026-07-06): the model, told nothing about local execution,
    fabricated "I run on a Vultr cloud server". Assert the fix: shell registered
    + tools on this turn → a factual local-device-access line, never the tool name."""
    block = CapabilityManifest.probe(
        _services(proactive=False, web=False, has_shell=True), tools_enabled=True
    ).render()
    low = block.lower()
    assert "device" in low
    assert "not" in low and "cloud" in low
    assert "shell" not in low


def test_system_exec_line_omitted_when_tools_off_this_turn() -> None:
    """Even with shell registered, a tool-free turn (conversational/clarify) must
    NOT claim system-exec access — the tool schema is not on the wire this turn,
    so asserting it would itself be a false claim."""
    block = CapabilityManifest.probe(
        _services(proactive=False, web=False, has_shell=True), tools_enabled=False
    ).render()
    assert block == ""


def test_system_exec_line_omitted_when_shell_not_registered() -> None:
    block = CapabilityManifest.probe(
        _services(proactive=False, web=False, has_shell=False), tools_enabled=True
    ).render()
    assert block == ""


def test_charter_carries_honesty_split() -> None:
    """(d) the charter forbids invented limitations and requires consequence-gating
    honesty — in BOTH the full and lean charters."""
    for charter in (behavioral_charter(), behavioral_charter_lean()):
        low = charter.lower()
        assert "never invent a limitation" in low
        assert "can't" in low  # the forbidden unverified "I can't"
        assert "consequence" in low
        assert "confirmation" in low


@pytest.mark.asyncio
async def test_manifest_injected_into_assembled_prompt() -> None:
    """(e) the manifest appears in the assembled system prompt when reachable, and
    is absent when nothing is reachable."""
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(name="cap", role="r", system_prompt="P", model_tier="fast")
    )
    state = PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="cap", pipeline_step="start",
    )
    from stackowl.pipeline.steps import assemble

    token = set_services(_services(proactive=True, web=True, reg=reg))
    try:
        out = await assemble.run(state)
        assert out.system_prompt is not None
        assert "act on a schedule and reach the person proactively" in out.system_prompt
    finally:
        reset_services(token)

    token = set_services(_services(proactive=False, web=False, reg=reg))
    try:
        out2 = await assemble.run(state)
        assert "act on a schedule and reach the person proactively" not in (
            out2.system_prompt or ""
        )
    finally:
        reset_services(token)
