"""synthesize_skills — trigger gap-analysis + skill-building on demand, mid-turn.

This is a THIN owl-tool wrapper around the EXISTING
:class:`SkillSynthesizerHandler` (``stackowl.skills.synthesizer_handler``). The
handler already runs the discover / refine / deprecate loop: it clusters the
agent's SUCCESSFUL tool-sequences, drafts NEW ``learned/`` skills from recurring
tactics, refines mid-tier skills, and deprecates low performers. It was previously
reachable ONLY as a daily scheduler job. This tool lets the agent reach the SAME
engine during a turn — so a tactic it just discovered can be captured as a reusable
skill immediately instead of waiting for the nightly cron.

REUSE, not reimplement: ``execute`` resolves the handler's deps off
:func:`get_services`, constructs the real handler (with
``skills_root=StackowlHome.skills_dir()``, exactly as the scheduler assembly
does), builds a synthetic manual :class:`Job`, ``await``\\s ``handler.execute(job)``,
and surfaces the handler's ``JobResult.output`` (``"created:N refined:N
deprecated:N"``) verbatim. No synthesis logic lives here.

Severity (operator decision): ``consequential`` — it AUTHORS skills under
``learned/`` that become the agent's OWN future system-prompt context (a
self-mutation surface, like ``skill_manage``). So it is consent-gated; the consent
gate fails closed off-TTY, which doubles as the cron / non-interactive protection.
``toolset_group="knowledge_write"`` — isolated from the READ ``knowledge`` group.

Self-healing (B5): a down/missing learning subsystem (no db / provider / skill
store / embeddings) degrades to a STRUCTURED failed ``ToolResult``, never a raise;
any handler exception is logged at ERROR and surfaced structurally (no hidden
errors).
"""

from __future__ import annotations

import time
import uuid

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.pipeline.services import get_services
from stackowl.scheduler.job import Job
from stackowl.skills.synthesizer_handler import SkillSynthesizerHandler
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_HANDLER_NAME = "skill_synthesizer"


class SynthesizeSkillsTool(Tool):
    """Trigger discover/refine/deprecate skill synthesis from your own successes."""

    @property
    def name(self) -> str:
        return "synthesize_skills"

    @property
    def description(self) -> str:
        return (
            "Trigger skill synthesis NOW: mine your OWN recent SUCCESSFUL "
            "tool-sequences for recurring tactics, author the strong ones as new "
            "reusable 'learned' skills (procedures you can follow next time), refine "
            "mid-tier learned skills, and deprecate low performers. Use this after "
            "you find a repeatable approach that worked, so it becomes a durable "
            "skill instead of being relearned. Returns how many skills were created, "
            "refined, and deprecated. "
            "LANE: turning your repeated SUCCESSES into reusable procedures. "
            "ANTI-LANE: do NOT use this to hand-write ONE specific skill (use "
            "skill_manage) or to learn from FAILURES (use reflect_now)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            commit_coupling="transactional",
            toolset_group="knowledge_write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info("synthesize_skills.execute: entry", extra={"_fields": {}})

        services = get_services()
        # 2. DECISION — require the synthesis subsystem deps; degrade structurally.
        missing = [
            label
            for label, dep in (
                ("db_pool", services.db_pool),
                ("provider_registry", services.provider_registry),
                ("skill_store", services.skill_store),
                ("embedding_registry", services.embedding_registry),
            )
            if dep is None
        ]
        if missing:
            return self._unavailable(", ".join(missing), t0)

        try:
            # 3. STEP — construct the REAL handler from services + a synthetic Job,
            # then call its existing .execute() (REUSE; no reimplementation here).
            handler = SkillSynthesizerHandler(
                db=services.db_pool,  # type: ignore[arg-type]
                provider_registry=services.provider_registry,  # type: ignore[arg-type]
                skill_store=services.skill_store,  # type: ignore[arg-type]
                skills_root=StackowlHome.skills_dir(),
                embedding_registry=services.embedding_registry,
                owl_registry=services.owl_registry,
                # Task 4 — thread the REAL consent gate through so a per-skill
                # gated write (stackowl.skills.authoring) can actually be
                # approved live (this call is itself already dispatched through
                # ConsequentialActionGate.check() for action_severity=
                # "consequential", but the inner write is a SEPARATE
                # consent-policy identity — see resolve_consent_identity()).
                # When called mid-turn (interactive TraceContext), the inner
                # write uses the LIVE identity/channel/session (normal
                # ALWAYS_ASK consent) — NOT the scheduled job's AUTO tier.
                consent_gate=services.consent_gate,
            )
            job = self._synthetic_job()
            result = await handler.execute(job)
        except Exception as exc:  # B5 — degrade, never raise; no hidden errors.
            log.tool.error(
                "synthesize_skills.execute: handler failed — structured degradation",
                exc_info=exc,
            )
            return self._err(f"skill synthesis failed: {type(exc).__name__}: {exc}", t0)

        if not result.success:
            return self._err(
                f"skill synthesis did not complete: {result.error or 'unknown error'}",
                t0,
            )
        output = result.output or "created:0 refined:0 deprecated:0"
        # 4. EXIT
        return self._ok(output, t0, metadata=result.metadata)

    @staticmethod
    def _synthetic_job() -> Job:
        """Build a minimal manual Job the handler can run (mirrors the scheduler)."""
        job_id = f"synthesize_skills-{uuid.uuid4().hex}"
        return Job(
            job_id=job_id,
            handler_name=_HANDLER_NAME,
            schedule="manual",
            idempotency_key=job_id,
            last_run_at=None,
            next_run_at="",
            status="running",
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _ok(output: str, t0: float, *, metadata: dict[str, object]) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "synthesize_skills.execute: exit",
            extra={"_fields": {"success": True, **metadata, "duration_ms": duration_ms}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "synthesize_skills.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

    @staticmethod
    def _unavailable(missing: str, t0: float) -> ToolResult:
        """Self-healing: a missing synthesis subsystem degrades to a structured
        FAILED ToolResult (so the model knows nothing was authored), never a raise."""
        msg = f"learning subsystem not wired: missing {missing}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "synthesize_skills.execute: subsystem unavailable — structured degradation",
            extra={"_fields": {"missing": missing, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
