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

from stackowl.commands.spec.submit import submit_command
from stackowl.infra.observability import log
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.knowledge.skill_commands import SYNTHESIZE, SkillSynthesizePayload


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
            command_types=("skill.synthesize",),
            commit_coupling="transactional",
            toolset_group="knowledge_write",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info("synthesize_skills.execute: entry", extra={"_fields": {}})

        services = get_services()
        # 2. DECISION — require the synthesis subsystem deps; degrade structurally
        # (the same checks skill_commands.py::_synthesize_handler repeats, so an
        # unwired subsystem is reported here without a round trip through the
        # command door first).
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

        # 3. STEP — AD-1: submit the declared command instead of constructing
        # SkillSynthesizerHandler and calling it directly. The handler
        # (skill_commands.py::_synthesize_handler) does the REAL synthesis run
        # (REUSE — no reimplementation there either, just relocated).
        db = services.db_pool
        assert db is not None  # narrowed by the `missing` check above
        submission = await submit_command(db, SYNTHESIZE, SkillSynthesizePayload())
        if submission.outcome is None:
            return self._ok(
                "Skill synthesis is pending approval.", t0, metadata={"pending": True},
            )
        if not submission.outcome.success:
            return self._err(
                submission.outcome.error or "skill synthesis did not complete.", t0,
            )
        result = submission.outcome.result
        output = str(result.get("output") or "created:0 refined:0 deprecated:0")
        metadata = {k: v for k, v in result.items() if k != "output"}
        # 4. EXIT
        return self._ok(output, t0, metadata=metadata)

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
