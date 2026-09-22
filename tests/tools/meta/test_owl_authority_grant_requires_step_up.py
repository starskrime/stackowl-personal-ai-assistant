"""Story 4.9, AC2 — "Given a command that widens an owl's authority
(owls.build.grant), when it is decided, then it is on the step-up list,
never approvable by voice, and never approvable by the owl it widens."

``owls.build.grant`` is declared ``severity="consequential"`` —
``authz.action_policy.decide()``'s own unconditional rule (checked BEFORE
reversibility or requester kind) is what satisfies this AC by construction:
CONSEQUENTIAL always needs step-up, for EVERY requester kind, with no
standing-authority carve-out (that carve-out only ever applies to an
irreversible, non-CONSEQUENTIAL command from an autonomous run — Story 4.6's
own "one carve-out, and it is narrow"). No new identity/voice plumbing is
introduced anywhere in this story (Boundaries) — "never approvable by voice"
and "never approvable by the owl it widens" both fall out of the SAME
unconditional check, since no owl/voice approver exists anywhere in this
architecture (approvals are always owner-mediated).
"""

from __future__ import annotations

import pytest

from stackowl.authz.action_policy import decide
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.skills.store import SkillIndexStore
from stackowl.tools.consent import ConsentPolicy, TrustTier
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.meta.owl_build_commands import GRANT
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry


class TestTheDeclaredSpecIsConsequential:
    def test_grant_is_registered_severity_consequential(self) -> None:
        spec = CommandSpecRegistry.get(GRANT)
        assert spec.severity == "consequential"

    def test_grant_has_no_undo(self) -> None:
        """Boundaries — no revoke mutator exists; inventing one is a
        separate, larger design decision this story explicitly defers."""
        spec = CommandSpecRegistry.get(GRANT)
        assert spec.reversible is False
        assert spec.undo_command_type is None


class TestDecideNeedsStepUpUnconditionally:
    """Drives the REAL, unmodified ``authz.action_policy.decide()`` (never
    reimplemented here) across every requester kind this codebase produces
    today, plus the one Story 4.6 carve-out that would otherwise bypass
    step-up for an irreversible command — proving CONSEQUENTIAL still wins."""

    @pytest.mark.parametrize("requester_kind", ["owner", "owl", "voice-unverified", "autonomous"])
    def test_every_requester_kind_needs_step_up(self, requester_kind: str) -> None:
        decision = decide(
            severity="consequential", reversible=False,
            requester_kind=requester_kind,  # type: ignore[arg-type]
        )
        assert decision.outcome == "needs_step_up"

    def test_a_matching_standing_authority_grant_does_not_bypass_it(self) -> None:
        """The ONE carve-out decide() has (autonomous + a matching grant ->
        run_at_once) never fires for CONSEQUENTIAL — severity is checked
        FIRST, unconditionally (module docstring: "severity outranks
        reversibility, always")."""
        decision = decide(
            severity="consequential", reversible=False, requester_kind="autonomous",
            authority_grant_id="some-real-grant-id",
        )
        assert decision.outcome == "needs_step_up"
        assert decision.authority_grant_id is None


class TestTheToolCallActuallyParks:
    """End-to-end: the REAL OwlBuildTool, a REAL registry + db, and consent
    already auto-approved at the TOOL level — proving the SECOND,
    command-level gate is what actually parks it (never bypassed by the
    tool's own consent having already said yes)."""

    @pytest.mark.asyncio
    async def test_grant_parks_even_when_tool_level_consent_already_approved(
        self, tmp_db: DbPool,
    ) -> None:
        narrow_ceiling = {"web_search"}
        registry = OwlRegistry.with_default_secretary()
        from stackowl.authz import BoundsSpec

        registry.register(
            OwlAgentManifest(
                name="mailbutler", role="assistant", system_prompt="s", model_tier="fast",
                bounds=BoundsSpec(tools=frozenset(narrow_ceiling)),
                creation_ceiling=BoundsSpec(tools=frozenset(narrow_ceiling | {"read_file"})),
                tools=["web_search"],
            ),
            source_name="t",
        )
        token = set_services(StepServices(
            tool_registry=ToolRegistry.with_defaults(),
            owl_registry=registry,
            # AUTO-trusted at the TOOL level — proves the command-level gate,
            # not the tool's own consent, is what parks this.
            consent_gate=ConsequentialActionGate(
                ConsentPolicy(tiers={"authority_widening": TrustTier.AUTO})
            ),
            stream_registry=StreamRegistry(),
            skill_store=SkillIndexStore(tmp_db),
            db_pool=tmp_db,
        ))
        trace = TraceContext.start(
            session_key="s", trace_id="t", interactive=True, channel="cli",
            delegation_depth=0, owl_name="secretary",
        )
        try:
            result = await OwlBuildTool().execute(
                action="grant", name="mailbutler", explicit_tools=["read_file"],
            )
        finally:
            TraceContext.reset(trace)
            reset_services(token)

        assert result.success, result.error
        assert "pending" in result.output.lower()
        # THE EFFECT never landed — never run_at_once (AC2's literal text).
        assert "read_file" not in set(registry.get("mailbutler").bounds.tools)
