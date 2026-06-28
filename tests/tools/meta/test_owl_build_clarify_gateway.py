"""Gateway-level integration journeys for owl_build's ADR-A resumable, validator-gated
creation — an UNDERSPECIFIED create ASKS the user (via the real ClarifyGateway) for the
missing required fields instead of erroring, then mints once complete.

Each journey drives the GENUINE :class:`OwlBuildTool` against a REAL
:class:`StepServices` (real :class:`OwlRegistry`, real consent gate, real authority
clamp + yaml persistence, real :class:`ClarifyGateway`). The ONLY faked things are the
AI provider (never consulted) and the TTY/consent toggles. So each journey FAILS if the
clarify wiring is removed.

C1 underspecified create ASKS then MINTS | C2 off-TTY underspecified FAILS CLOSED (no
hang, nothing minted) | C3 a complete spec still mints in ONE shot (no clarify asked).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.trace import TraceContext
from stackowl.interaction.clarify_gateway import ClarifyGateway
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

pytestmark = pytest.mark.usefixtures("_live_io")


# --- scaffolding ------------------------------------------------------------


class _ScriptedProvider:
    """A registry-shaped AI provider stand-in. owl_build never consults the model."""

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


class _FakeAdapter:
    """Records the clarify questions delivered to the channel."""

    def __init__(self, name: str = "cli") -> None:
        self._name = name
        self.calls: list[tuple[str, str, tuple[str, ...], str]] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def send_clarify(
        self, session_id: str, question: str, choices: tuple[str, ...], clarify_id: str,
    ) -> None:
        self.calls.append((session_id, question, tuple(choices), clarify_id))


def _services(
    tmp_db: DbPool,
    *,
    registry: OwlRegistry,
    gateway: ClarifyGateway,
) -> StepServices:
    """REAL StepServices with the owl registry, AUTO consent (so a completed build is
    auto-approved), and the real clarify gateway. Only the provider is faked."""
    return StepServices(
        provider_registry=_ScriptedProvider(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry.with_defaults(),
        owl_registry=registry,
        consent_gate=ConsequentialActionGate(
            ConsentPolicy(tiers={"owl_build": TrustTier.AUTO})
        ),
        stream_registry=StreamRegistry(),
        skill_store=SkillIndexStore(tmp_db),
        clarify_gateway=gateway,
        db_pool=tmp_db,
    )


def _gateway() -> tuple[ClarifyGateway, _FakeAdapter]:
    gw = ClarifyGateway()
    adapter = _FakeAdapter("cli")
    gw.register_adapter("cli", adapter)  # type: ignore[arg-type]
    return gw, adapter


_SESSION = "s-owl-build"


async def _run(
    services: StepServices,
    args: dict,
    *,
    interactive: bool,
    channel: str | None,
    clarify_timeout_s: float = 5.0,
):
    """Drive one REAL owl_build.execute() turn inside the trace + service context."""
    svc_token = set_services(services)
    trace_token = TraceContext.start(
        session_id=_SESSION if (interactive and channel) else None,
        trace_id="t-owl-build",
        interactive=interactive,
        channel=channel,
        delegation_depth=0,
        owl_name="secretary",
    )
    try:
        return await OwlBuildTool(clarify_timeout_s=clarify_timeout_s).execute(**args)
    finally:
        TraceContext.reset(trace_token)
        reset_services(svc_token)


async def _resolve_when_parked(gateway: ClarifyGateway, answer: str) -> None:
    """Poll the cooperative loop until owl_build has parked a clarify, then answer it."""
    for _ in range(500):
        await asyncio.sleep(0)
        if gateway.peek_for_session(_SESSION, "cli") is not None:
            assert gateway.try_resolve(_SESSION, "cli", answer) is not None
            return
    raise AssertionError("owl_build never parked a clarify question")


# --- C1: underspecified create ASKS then MINTS ------------------------------


async def test_c1_underspecified_create_asks_then_mints(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    registry = OwlRegistry.with_default_secretary()
    gateway, adapter = _gateway()
    services = _services(tmp_db, registry=registry, gateway=gateway)

    # No specialty → validator reports it missing → the tool ASKS for it.
    task = asyncio.ensure_future(
        _run(
            services,
            {"action": "create", "name": "scout", "preset": "researcher"},
            interactive=True,
            channel="cli",
        )
    )
    await _resolve_when_parked(gateway, "recon for the team")
    result = await task

    # OUTCOME — it asked exactly the missing field, then minted live.
    assert result.success, result.error
    assert len(adapter.calls) == 1, adapter.calls
    assert "role" in adapter.calls[0][1].lower()  # the specialty question
    live = registry.get("scout")
    assert live.origin == "agent"
    from stackowl.commands.config_helpers import config_path

    assert config_path().exists(), "minted owl was not persisted"


# --- C2: off-TTY underspecified FAILS CLOSED (no hang) ----------------------


async def test_c2_off_tty_underspecified_fails_closed(
    tmp_home: Path, tmp_db: DbPool
) -> None:
    from stackowl.commands.config_helpers import config_path

    registry = OwlRegistry.with_default_secretary()
    gateway, adapter = _gateway()
    services = _services(tmp_db, registry=registry, gateway=gateway)

    # Underspecified AND no interactive user — must refuse immediately, never park.
    result = await asyncio.wait_for(
        _run(
            services,
            {"action": "create", "name": "scout", "preset": "researcher"},
            interactive=False,
            channel=None,
        ),
        timeout=2.0,  # the call itself proves no hang
    )

    assert not result.success
    assert "missing" in (result.error or "").lower()
    assert adapter.calls == [], "off-TTY must not deliver a clarify"
    with pytest.raises(OwlNotFoundError):
        registry.get("scout")
    assert not config_path().exists(), "off-TTY refusal still wrote to yaml"


# --- C3: a complete spec still mints in ONE shot (back-compat) ---------------


async def test_c3_complete_spec_mints_one_shot(tmp_home: Path, tmp_db: DbPool) -> None:
    registry = OwlRegistry.with_default_secretary()
    gateway, adapter = _gateway()
    services = _services(tmp_db, registry=registry, gateway=gateway)

    result = await _run(
        services,
        {"action": "create", "name": "scout", "preset": "researcher", "specialty": "recon"},
        interactive=True,
        channel="cli",
    )

    assert result.success, result.error
    assert adapter.calls == [], "a complete spec must not ask anything"
    assert registry.get("scout").origin == "agent"
