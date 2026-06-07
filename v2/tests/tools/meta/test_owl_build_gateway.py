"""Gateway-level integration journeys for owl_build (Phase-2 A) — the self-extending
owl-builder meta-tool.

Each journey drives the GENUINE :class:`OwlBuildTool` against a REAL
:class:`StepServices` (real :class:`OwlRegistry`, real :class:`ConsequentialActionGate`
+ :class:`ConsentPolicy`, real authority clamp, real yaml persistence + boot reload).
The ONLY things faked are the AI provider (a registry-shaped scripted stand-in, never
actually consulted by owl_build) and the TTY/consent toggles (interactive/channel +
the AUTO trust tier). So each journey FAILS if the corresponding wiring is removed.

We assert OUTCOMES — the owl is persisted / clamped / absent on disk and in the live
registry — not a return-string shape (a result.success + key-substring check is the
spoken surface, the registry/yaml are the ground truth).

J1 mint→persist→survive-restart | J2a unbounded creator drops shell | J2b off-TTY
refuses | J3a cannot retire secretary | J3b cannot edit a human owl | J4 sub-agent
(depth>0) cannot mint | J5 narrow creator's floor clamps the new owl.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.authz.bounds import BoundsSpec
from stackowl.config.settings import Settings
from stackowl.db.pool import DbPool
from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.trace import TraceContext
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.owl_revalidator import revalidate_agent_owls
from stackowl.owls.registry import _SECRETARY_NAME, OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.meta.owl_build_authz import SAFE_DEFAULT_CEILING
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


# --- scaffolding ------------------------------------------------------------


class _ScriptedProvider:
    """A registry-shaped AI provider stand-in. owl_build never consults the model
    (it is a deterministic security tool), so this exists purely so StepServices is
    wired exactly as in production — the ONLY faked dependency."""

    protocol = "anthropic"

    async def complete_with_tools(self, *a, **k):  # pragma: no cover - never called
        return ("", [])

    async def complete(self, *a, **k):  # pragma: no cover
        return ""

    async def stream(self, *a, **k):  # pragma: no cover
        if False:
            yield ""

    def get(self, name: str) -> _ScriptedProvider:  # registry-shaped
        return self

    def get_by_tier(self, tier: str) -> _ScriptedProvider:
        return self


def _gate(*, auto: bool) -> ConsequentialActionGate:
    """A REAL gate. AUTO trust for owl_build auto-approves consent; otherwise the
    default (ALWAYS_ASK → FailClosedPrompter) denies."""
    tiers = {"owl_build": TrustTier.AUTO} if auto else {}
    return ConsequentialActionGate(ConsentPolicy(tiers=tiers))


def _services(
    tmp_db: DbPool, *, registry: OwlRegistry, auto_consent: bool
) -> StepServices:
    """Build a REAL StepServices with the owl registry + consent gate + skill store
    the tool reads at execute time. Only the provider is faked."""
    return StepServices(
        provider_registry=_ScriptedProvider(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=registry,
        consent_gate=_gate(auto=auto_consent),
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        db_pool=tmp_db,
    )


def _registry_with_secretary() -> OwlRegistry:
    """A fresh registry holding ONLY the mandatory (unbounded) Secretary."""
    return OwlRegistry.with_default_secretary()


async def _run(
    services: StepServices,
    args: dict,
    *,
    interactive: bool,
    channel: str | None,
    owl_name: str,
    delegation_depth: int = 0,
    creation_ceiling: BoundsSpec | None = None,
):
    """Drive a single REAL owl_build.execute() turn inside the trace + service
    context the tool reads (TTY/channel/session/owl/depth/ceiling + get_services)."""
    svc_token = set_services(services)
    trace_token = TraceContext.start(
        session_id="s-owl-build" if (interactive and channel) else None,
        trace_id="t-owl-build",
        interactive=interactive,
        channel=channel,
        delegation_depth=delegation_depth,
        owl_name=owl_name,
        creation_ceiling=creation_ceiling,
    )
    try:
        return await OwlBuildTool().execute(**args)
    finally:
        TraceContext.reset(trace_token)
        reset_services(svc_token)


def _settings_for_reload() -> Settings:
    """A fresh Settings reading the SAME tmp yaml the tool persisted to (the yaml
    path derives from STACKOWL_HOME, isolated by the tmp_home fixture)."""
    return Settings()


# --- J1: root mints a researcher → persists → survives restart --------------


async def test_j1_root_mints_persists_survives_restart(tmp_home: Path, tmp_db: DbPool) -> None:
    registry = _registry_with_secretary()
    services = _services(tmp_db, registry=registry, auto_consent=True)

    result = await _run(
        services,
        {"action": "create", "name": "scout", "preset": "researcher", "specialty": "recon"},
        interactive=True,
        channel="cli",
        owl_name="secretary",
    )

    # OUTCOME — minted live with agent provenance.
    assert result.success, result.error
    live = registry.get("scout")
    assert live.origin == "agent"
    assert live.created_by == "secretary"

    # OUTCOME — persisted to disk (the durable record at the tmp-home yaml).
    from stackowl.commands.config_helpers import config_path

    assert config_path().exists(), "owl was not persisted to yaml"

    # OUTCOME — survives a simulated restart: fresh registry from the SAME yaml +
    # the boot re-clamp pass. Bounds remain a subset of SAFE_DEFAULT_CEILING.
    reloaded = OwlRegistry.from_settings(_settings_for_reload())
    revalidate_agent_owls(reloaded)
    survived = reloaded.get("scout")
    assert survived.origin == "agent"
    ceiling = SAFE_DEFAULT_CEILING.tools or frozenset()
    assert survived.bounds is not None
    assert (survived.bounds.tools or frozenset()) <= ceiling, (
        f"reloaded scout bounds escaped SAFE_DEFAULT_CEILING: {sorted(survived.bounds.tools or [])}"
    )


# --- J2a: unbounded creator drops shell without widening --------------------


async def test_j2a_unbounded_creator_drops_shell(tmp_home: Path, tmp_db: DbPool) -> None:
    registry = _registry_with_secretary()
    services = _services(tmp_db, registry=registry, auto_consent=True)

    result = await _run(
        services,
        {
            "action": "create",
            "name": "coder",
            "explicit_tools": ["read_file", "shell"],
            "specialty": "builds",
        },
        interactive=True,
        channel="cli",
        owl_name="secretary",
    )

    assert result.success, result.error
    coder = registry.get("coder")
    assert coder.bounds is not None
    # SAFE_DEFAULT_CEILING (read-only-ish) dropped shell — the human must widen it.
    assert "shell" not in (coder.bounds.tools or frozenset())
    assert "read_file" in (coder.bounds.tools or frozenset())
    assert "shell" in result.output.lower() and "dropped" in result.output.lower(), result.output


# --- J2b: off-TTY refuses entirely (fail closed) ----------------------------


async def test_j2b_off_tty_refuses(tmp_home: Path, tmp_db: DbPool) -> None:
    from stackowl.commands.config_helpers import config_path

    registry = _registry_with_secretary()
    # AUTO tier is wired, but the in-tool no-user-present check still fails closed.
    services = _services(tmp_db, registry=registry, auto_consent=True)

    result = await _run(
        services,
        {"action": "create", "name": "scout", "preset": "researcher", "specialty": "recon"},
        interactive=False,
        channel=None,
        owl_name="secretary",
    )

    assert not result.success
    assert "refused" in (result.error or "").lower()
    # OUTCOME — nothing minted, nothing persisted.
    with pytest.raises(OwlNotFoundError):
        registry.get("scout")
    assert not config_path().exists(), "off-TTY refusal still wrote to yaml"


# --- J3a: cannot retire the secretary ---------------------------------------


async def test_j3a_cannot_retire_secretary(tmp_home: Path, tmp_db: DbPool) -> None:
    registry = _registry_with_secretary()
    services = _services(tmp_db, registry=registry, auto_consent=True)

    result = await _run(
        services,
        {"action": "retire", "name": _SECRETARY_NAME},
        interactive=True,
        channel="cli",
        owl_name="secretary",
    )

    assert not result.success
    # OUTCOME — the mandatory secretary is still present.
    assert registry.has_secretary()
    assert registry.get(_SECRETARY_NAME) is not None


# --- J3b: cannot edit a human owl -------------------------------------------


async def test_j3b_cannot_edit_human_owl(tmp_home: Path, tmp_db: DbPool) -> None:
    registry = _registry_with_secretary()
    human = OwlAgentManifest(
        name="planner",
        role="planning",
        system_prompt="p",
        model_tier="standard",
        origin="human",
        bounds=BoundsSpec(tools=frozenset({"read_file"})),
    )
    registry.register(human, source_name="human")
    services = _services(tmp_db, registry=registry, auto_consent=True)

    result = await _run(
        services,
        {"action": "edit", "name": "planner", "preset": "researcher", "specialty": "x"},
        interactive=True,
        channel="cli",
        owl_name="secretary",
    )

    assert not result.success
    assert "human" in (result.error or "").lower()
    # OUTCOME — the human owl is unchanged (origin + its original bounds).
    after = registry.get("planner")
    assert after.origin == "human"
    assert (after.bounds.tools or frozenset()) == frozenset({"read_file"})


# --- J4: a sub-agent (depth>0) cannot mint ----------------------------------


async def test_j4_subagent_cannot_mint(tmp_home: Path, tmp_db: DbPool) -> None:
    registry = _registry_with_secretary()
    services = _services(tmp_db, registry=registry, auto_consent=True)

    result = await _run(
        services,
        {"action": "create", "name": "scout", "preset": "researcher", "specialty": "recon"},
        interactive=True,
        channel="cli",
        owl_name="secretary",
        delegation_depth=1,  # a delegated sub-agent
    )

    assert not result.success
    assert "root" in (result.error or "").lower()
    # OUTCOME — no owl minted by a sub-agent.
    with pytest.raises(OwlNotFoundError):
        registry.get("scout")


# --- J5: a narrow creator's floor clamps the new owl ------------------------


async def test_j5_narrow_creator_floor_clamps_child(tmp_home: Path, tmp_db: DbPool) -> None:
    registry = _registry_with_secretary()
    narrow = OwlAgentManifest(
        name="narrow",
        role="narrow",
        system_prompt="p",
        model_tier="fast",
        origin="agent",
        created_by="secretary",
        creation_ceiling=BoundsSpec(tools=frozenset({"read_file"})),
        bounds=BoundsSpec(tools=frozenset({"read_file"})),
    )
    registry.register(narrow, source_name="agent_owls")
    services = _services(tmp_db, registry=registry, auto_consent=True)

    result = await _run(
        services,
        {
            "action": "create",
            "name": "probe",
            "explicit_tools": ["read_file", "web_fetch"],
            "specialty": "recon",
        },
        interactive=True,
        channel="cli",
        owl_name="narrow",  # the narrow owl is the creator
    )

    assert result.success, result.error
    probe = registry.get("probe")
    assert probe.bounds is not None
    tools = probe.bounds.tools or frozenset()
    # Clamped to the NARROW delegator's floor ({read_file}), NOT the safe default —
    # web_fetch (in SAFE_DEFAULT_CEILING) is still dropped because the creator lacks it.
    assert tools <= frozenset({"read_file"}), f"probe escaped the narrow floor: {sorted(tools)}"
    assert "web_fetch" not in tools
