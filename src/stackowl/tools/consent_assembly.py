"""ConsentAssembly — wires the consent gate + per-channel prompter routing (OPS-5 / F149).

Extracted verbatim from the ~1700-line ``_phase_gateway`` monolith so the
consequential-action consent boundary is wired in ONE cohesive, seam-testable
unit. Mirrors :class:`MemoryAssembly` / :class:`SandboxAssembly`.

The :class:`RoutingPrompter` is intentionally mutable so the Telegram/Slack
prompters can register AFTER their adapters start; the CLI gets the TTY prompter
immediately. The returned :class:`ConsequentialActionGate` wraps a
:class:`ConsentPolicy` over that routing prompter and the audit logger.
``install_default_translations()`` seeds the consent button/label i18n catalog
(English copy is the single source of truth; other locales register later).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.audit.logger import AuditLogger
    from stackowl.tools.consent import RoutingPrompter
    from stackowl.tools.registry import ConsequentialActionGate


@dataclass(frozen=True)
class ConsentComponents:
    """Frozen container for the wired consent subsystem."""

    routing_prompter: RoutingPrompter
    consent_gate: ConsequentialActionGate


class ConsentAssembly:
    """Factory that wires the consent gate + per-channel prompter routing."""

    @staticmethod
    def build(audit_logger: AuditLogger) -> ConsentComponents:
        """Build the consent gate over a CLI-registered routing prompter."""
        log.infra.info("[consent] assembly.build: entry")

        # Deferred imports — keep this module cheap when consent isn't used.
        from stackowl.tools.consent import (
            ConsentPolicy,
            RoutingPrompter,
            TrustTier,
            TtyConsentPrompter,
        )
        from stackowl.tools.registry import ConsequentialActionGate
        from stackowl.tui.i18n_strings import install_default_translations

        # Consent button/label catalog — English copy lives in the i18n catalog
        # (single source of truth); other locales can be registered later.
        install_default_translations()

        routing_prompter = RoutingPrompter()
        routing_prompter.register("cli", TtyConsentPrompter())

        # Task 4 Finding 2 (user decision) — the daily SkillSynthesizer job is
        # genuinely unattended (no human ever present to approve a prompt), so
        # its DEDICATED scheduled identity is auto-trusted here. security_scan_gate
        # still runs unconditionally first (stackowl.skills.authoring); AUTO only
        # skips the human PROMPT. The LIVE identity (used when a human IS present,
        # e.g. the synthesize_skills tool mid-turn) is deliberately NOT in this
        # dict — it stays on normal ALWAYS_ASK consent.
        # Task 5 review (whole-branch pass, same user decision as Task 4) —
        # FailureOutcomeMiner is ALSO genuinely unattended: it is only ever
        # invoked from a scheduler tick (IncidentEscalationHandler._consume_verdict),
        # never a live turn, so its scheduled identity gets the SAME auto-trust.
        # Without this it was silently DENIED every time (ALWAYS_ASK -> no
        # "scheduler" prompter registered -> fail closed) even though
        # security_scan_gate still runs unconditionally regardless of tier.
        from stackowl.learning.failure_outcome_miner import (
            _CONSENT_TOOL_NAME_SCHEDULED as _FAILURE_MINER_CONSENT_TOOL_NAME_SCHEDULED,
        )
        from stackowl.skills.synthesizer import _CONSENT_TOOL_NAME_SCHEDULED

        tiers = {
            _CONSENT_TOOL_NAME_SCHEDULED: TrustTier.AUTO,
            _FAILURE_MINER_CONSENT_TOOL_NAME_SCHEDULED: TrustTier.AUTO,
        }
        consent_gate = ConsequentialActionGate(
            ConsentPolicy(prompter=routing_prompter, audit_logger=audit_logger, tiers=tiers)
        )

        log.infra.info("[consent] assembly.build: exit — gate + routing prompter ready")
        return ConsentComponents(routing_prompter=routing_prompter, consent_gate=consent_gate)
