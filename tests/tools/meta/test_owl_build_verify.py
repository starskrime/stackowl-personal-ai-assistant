"""TS2 — creation self-verification (ADR-T2). The core honesty fix.

A ``creates_persistent_entity`` tool must set ``ToolResult.verified`` by RE-READING
the world after the write, NEVER from its own ``success`` flag. This is exactly the
"0 owls created but ✅ deployed" bug: ``success=True`` with a failed world-read MUST
yield ``verified=False``.

We drive the GENUINE :class:`OwlBuildTool` against a REAL registry + yaml + db (only
the AI provider is faked — owl_build never consults it). Case (a) goes through the
full ``__call__`` verification seam; the negative cases unit-test ``verify()`` against
a crafted result so we can stage a failed read deterministically.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.base import ToolResult
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


class _ScriptedProvider:
    """Registry-shaped provider stand-in — owl_build never consults the model."""

    protocol = "anthropic"

    async def complete_with_tools(self, *a, **k):  # pragma: no cover
        return ("", [])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""

    def get(self, name: str) -> _ScriptedProvider:
        return self

    def get_by_tier(self, tier: str) -> _ScriptedProvider:
        return self


def _services(tmp_db: DbPool, registry: OwlRegistry) -> StepServices:
    return StepServices(
        provider_registry=_ScriptedProvider(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=registry,
        consent_gate=ConsequentialActionGate(
            ConsentPolicy(tiers={"owl_build": TrustTier.AUTO})
        ),
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        db_pool=tmp_db,
    )


async def test_a_create_success_is_verified_via_world_reads(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """(a) A genuine create → verified=True, AND the world actually holds the owl
    (the live registry + the persisted yaml were really consulted)."""
    registry = OwlRegistry.with_default_secretary()
    services = _services(tmp_db, registry)
    token = set_services(services)
    trace = TraceContext.start(
        session_id="s", trace_id="t", interactive=True, channel="cli",
        delegation_depth=0, owl_name="secretary",
    )
    try:
        # Drive through __call__ so the verification SEAM runs (not bare execute()).
        result = await OwlBuildTool()(
            action="create", name="scout", preset="researcher", specialty="recon",
        )
    finally:
        TraceContext.reset(trace)
        reset_services(token)

    assert result.success, result.error
    assert result.verified is True, "a real create must MEASURE verified=True"
    # The reads verify() consulted are genuinely satisfied:
    from stackowl.commands.config_helpers import config_path, load_yaml

    assert registry.get("scout").origin == "agent"          # live registry read
    owls = load_yaml(config_path()).get("owls") or []        # persisted yaml read
    assert any(e.get("name") == "scout" for e in owls)


async def test_b_success_but_owl_absent_yields_verified_false(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """(b) ok=True but the registry does NOT contain the owl afterward → verified
    MUST be False (the "0 owls but ✅" bug). verify() re-reads; it does not trust ok."""
    registry = OwlRegistry.with_default_secretary()  # no 'ghost' owl
    services = _services(tmp_db, registry)
    token = set_services(services)
    try:
        tool = OwlBuildTool()
        # A success the tool ASSERTED, naming an owl that was never registered.
        claimed = ToolResult(
            success=True, output="Created owl 'ghost'.", duration_ms=1.0,
            artifact_path="ghost",
        )
        verdict = await tool.verify({}, claimed, started_at=time.time())
    finally:
        reset_services(token)
    assert verdict is False


async def test_b2_in_registry_but_not_persisted_yields_false(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """The yaml read is REAL, not skipped: an owl live in the registry but absent
    from the persisted yaml (a half-committed create) → verified=False."""
    from stackowl.owls.manifest import OwlAgentManifest

    registry = OwlRegistry.with_default_secretary()
    # Register live WITHOUT writing yaml — read 1 passes, read 2 (yaml) fails.
    ghost = OwlAgentManifest(
        name="halfowl", role="recon", origin="agent",
        system_prompt="recon owl", model_tier="standard",
    )
    registry.register(ghost, source_name="agent_owls")
    services = _services(tmp_db, registry)
    token = set_services(services)
    try:
        claimed = ToolResult(
            success=True, output="Created owl 'halfowl'.", duration_ms=1.0,
            artifact_path="halfowl",
        )
        verdict = await OwlBuildTool().verify({}, claimed, started_at=time.time())
    finally:
        reset_services(token)
    assert verdict is False


async def test_c_edit_retire_results_are_not_verified(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    """verify() is scoped to create: a result with no stamped artifact (edit/retire)
    returns None ⇒ byte-identical (falls back to success)."""
    registry = OwlRegistry.with_default_secretary()
    services = _services(tmp_db, registry)
    token = set_services(services)
    try:
        no_artifact = ToolResult(success=True, output="Updated owl.", duration_ms=1.0)
        verdict = await OwlBuildTool().verify({}, no_artifact, started_at=time.time())
    finally:
        reset_services(token)
    assert verdict is None
