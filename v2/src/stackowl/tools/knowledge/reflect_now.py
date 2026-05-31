"""reflect_now — trigger self-learning (Reflexion) on demand, mid-turn.

This is a THIN owl-tool wrapper around the EXISTING
:class:`ReflectionWriterHandler` (``stackowl.memory.reflection_writer_handler``).
The handler already turns failed / low-quality task outcomes into Reflexion-style
reflections; it was previously reachable ONLY as a background scheduler job (every
~15 min). This tool lets the agent reach the SAME engine during a turn — so it can
learn from a just-finished sub-task instead of waiting for the nightly cron.

REUSE, not reimplement: ``execute`` resolves the handler's deps off
:func:`get_services`, constructs the real handler, builds a synthetic manual
:class:`Job`, ``await``\\s ``handler.execute(job)``, and surfaces the handler's
``JobResult.output`` (e.g. ``"written:N"``) verbatim. No reflection logic lives
here.

Severity (operator decision): ``read`` — it ANALYZES the agent's own past
outcomes and writes reflections into the learning store (not the user's data and
not an external side effect), so it is never consent-gated. ``toolset_group=
"knowledge"`` — beside the other READ knowledge tools.

Self-healing (B5): a down/missing learning subsystem (no db / provider /
embeddings / lessons index) degrades to a STRUCTURED failed ``ToolResult``, never
a raise; any handler exception is logged at ERROR and surfaced as a structured
failure (no hidden errors).
"""

from __future__ import annotations

import time
import uuid

from stackowl.infra.observability import log
from stackowl.memory.reflection_writer_handler import ReflectionWriterHandler
from stackowl.pipeline.services import get_services
from stackowl.scheduler.job import Job
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_HANDLER_NAME = "reflection_writer"


class ReflectNowTool(Tool):
    """Trigger Reflexion self-learning on the agent's recent outcomes, on demand."""

    @property
    def name(self) -> str:
        return "reflect_now"

    @property
    def description(self) -> str:
        return (
            "Trigger self-learning NOW: run a Reflexion pass over your own recent "
            "failed or low-quality task outcomes, distilling each into a reflection "
            "(what went wrong + a suggested strategy) saved to the learning store "
            "for future retrieval. Use this after a hard or botched sub-task so you "
            "improve immediately instead of waiting for the nightly job. Returns how "
            "many reflections were written. "
            "LANE: learning from your OWN recent performance. "
            "ANTI-LANE: do NOT use this to remember a user FACT (use memory) or to "
            "author a reusable procedure (use synthesize_skills / skill_manage)."
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
            action_severity="read",
            toolset_group="knowledge",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info("reflect_now.execute: entry", extra={"_fields": {}})

        services = get_services()
        # 2. DECISION — require the learning subsystem deps; degrade structurally.
        missing = [
            label
            for label, dep in (
                ("db_pool", services.db_pool),
                ("provider_registry", services.provider_registry),
                ("embedding_registry", services.embedding_registry),
            )
            if dep is None
        ]
        if missing:
            return self._unavailable(", ".join(missing), t0)

        try:
            # 3. STEP — construct the REAL handler from services + a synthetic Job,
            # then call its existing .execute() (REUSE; no reimplementation here).
            handler = ReflectionWriterHandler(
                db=services.db_pool,  # type: ignore[arg-type]
                provider_registry=services.provider_registry,  # type: ignore[arg-type]
                embedding_registry=services.embedding_registry,  # type: ignore[arg-type]
                lessons_index=services.lessons_index,
            )
            job = self._synthetic_job()
            result = await handler.execute(job)
        except Exception as exc:  # B5 — degrade, never raise; no hidden errors.
            log.tool.error(
                "reflect_now.execute: handler failed — structured degradation",
                exc_info=exc,
            )
            return self._err(f"reflection failed: {type(exc).__name__}: {exc}", t0)

        if not result.success:
            return self._err(
                f"reflection did not complete: {result.error or 'unknown error'}", t0,
            )
        output = result.output or "written:0"
        # 4. EXIT
        return self._ok(output, t0, written=result.metadata.get("written"))

    @staticmethod
    def _synthetic_job() -> Job:
        """Build a minimal manual Job the handler can run (mirrors the scheduler)."""
        job_id = f"reflect_now-{uuid.uuid4().hex}"
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
    def _ok(output: str, t0: float, *, written: object = None) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "reflect_now.execute: exit",
            extra={"_fields": {"success": True, "written": written, "duration_ms": duration_ms}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "reflect_now.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

    @staticmethod
    def _unavailable(missing: str, t0: float) -> ToolResult:
        """Self-healing: a missing learning subsystem degrades to a structured
        FAILED ToolResult (so the model knows nothing was learned), never a raise."""
        msg = f"learning subsystem not wired: missing {missing}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "reflect_now.execute: subsystem unavailable — structured degradation",
            extra={"_fields": {"missing": missing, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
