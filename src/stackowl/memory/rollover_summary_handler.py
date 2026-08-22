"""RolloverSummaryHandler — what a conversation boundary does with memory (D01.7).

WHAT IT DOES: writes ONE narrative artifact per ended conversation — what was
decided, what went wrong.

WHAT IT USED TO ALSO DO, and no longer does (D08.1). It nudged
``ConversationMiner`` at the conversation boundary, so fact extraction got the
right moment rather than a fixed interval. That leg is GONE with the extraction
pipeline itself: measured 2026-08-08, that pipeline had produced 88,631 facts of
which 37.1% mentioned a trace id or ``failure_class`` — the platform's own
diagnostics stored as durable memory about the user — because the extractor took
``session_key`` and never consulted it, so every incident-recovery and retry
conversation was mined as if a human had said it.

THE NARRATIVE SURVIVES, and that is the point of reading this before deleting
anything here. The old docstring called it "the part the miner does not produce":
the miner made atomic facts ABOUT the user and no account of a conversation's
decisions or failures. Removing the extraction pipeline must not remove the one
thing that was never duplicated.

COST. Every rolled lane that has a transcript gets one standard-tier call, and the
model itself decides whether anything was worth keeping (Bakir superseded the
structural notability gate, whose only available signal turned out to be a counter
that counted nothing). A lane that said nothing has no transcript, so it costs
nothing — that is the whole control.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from stackowl.infra.observability import log
from stackowl.memory.models import StagedFact
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.providers.base import Message
from stackowl.scheduler.base import JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool

HANDLER_NAME = "rollover_summary"

#: The artifact's source type. It was exempt from the promoter's corroboration
#: gate because nothing ever re-derives it; that gate went with the promoter in
#: D08.2 seam 3 pass 4.
#:
#: Typed as the Literal rather than ``str`` so mypy checks it against
#: ``StagedFact.source_type``: a typo here would otherwise only surface as a CHECK
#: constraint violation at runtime, inside a 4 AM background job.
SUMMARY_SOURCE_TYPE: Literal["conversation_summary"] = "conversation_summary"

#: Authored deliberately, not inferred from evidence, so there is no uncertainty
#: to express. Also keeps the artifact above any configured
#: ``promotion_confidence_threshold`` — a summary that silently stopped being
#: promoted because someone raised a threshold would be the same dormancy trap
#: again, one config key further away.
SUMMARY_CONFIDENCE = 1.0

_PROMPT_DIR = Path(__file__).parent / "prompts"
_TEMPLATE_NAME = "conversation_summary.j2"
_FENCE_OPEN_RE = re.compile(r"^\s*```(?:[A-Za-z0-9_-]+)?\s*\n?", re.UNICODE)
_FENCE_CLOSE_RE = re.compile(r"\n?```\s*$", re.UNICODE)

#: A safety ceiling, not a window. The whole ended incarnation is summarised (a
#: day is the unit), and this only stops one runaway lane handing an unbounded
#: transcript to a single call.
_MAX_MESSAGES = 400

# Scoped to the CONVERSATION'S OWN owner, not to a constructor default.
# `messages` is owner-governed (migration 0043), and matching on
# conversation_id alone would summarise a foreign row that merely claims the
# same conversation into this owner's day. Deriving the owner from the
# conversation row keeps this correct under multiple principals WITHOUT the
# handler having to know whose boundary it is — a default would read nothing
# for every non-default principal, which is the silent no-op trap documented
# on any_active_task_for_lane.
_TRANSCRIPT_SQL = """
SELECT role, content
FROM messages
WHERE conversation_id = ?
  AND owner_id = (SELECT owner_id FROM conversations WHERE id = ?)
ORDER BY created_at ASC, id ASC
LIMIT ?
"""


class RolloverSummaryHandler(JobHandler):
    """Runs once per conversation boundary, off a durable job row."""

    _handler_name: ClassVar[str] = HANDLER_NAME

    def __init__(
        self,
        *,
        db: DbPool,
        bridge: object,
        provider_registry: object,
        summary_tier: str = "standard",
        max_messages: int = _MAX_MESSAGES,
    ) -> None:
        self._db = db
        self._bridge = bridge
        self._providers = provider_registry
        self._tier = summary_tier
        self._max_messages = max_messages
        self._env = Environment(
            loader=FileSystemLoader(str(_PROMPT_DIR)),
            autoescape=select_autoescape(enabled_extensions=(), default=False),
            undefined=StrictUndefined,
        )

    @property
    def handler_name(self) -> str:
        return self._handler_name

    @property
    def trigger_kind(self) -> TriggerKind:
        # Enqueued per boundary by the session.rollover consumer; there is no
        # standing job row, so this declares on_demand and the boot wiring audit
        # does not flag the absence of a seed.
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        params = dict(job.params or {})
        lane = str(params.get("session_key") or "")
        ended = str(params.get("ended_conversation_id") or "")
        identity = params.get("identity_key") or None
        # The OWNER scope, not the lane. See the module docstring.
        scope = str(identity or lane)

        # 1. ENTRY
        log.memory.info(
            "[memory] rollover_summary.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "session_key": lane,
                               "ended_conversation_id": ended, "scope": scope,
                               "reason": params.get("reason")}},
        )

        if not lane or not ended:
            # Never guess which conversation ended: a summary attached to the
            # wrong incarnation is worse than no summary.
            return self._failed(
                job, t0, "malformed job: session_key and ended_conversation_id are required",
                mined=0,
            )

        transcript = await self._read_transcript(ended)
        # 2. DECISION — a lane that said nothing costs nothing.
        if not transcript:
            log.memory.info(
                "[memory] rollover_summary.execute: exit — no transcript, nothing to do",
                extra={"_fields": {"job_id": job.job_id, "ended_conversation_id": ended}},
            )
            return JobResult(
                job_id=job.job_id, effect_class="state_change", success=True,
                output="no_transcript", error=None,
                duration_ms=(time.monotonic() - t0) * 1000,
                metadata={"mined": 0, "summary_staged": False,
                          "summary_parsed": None, "messages": 0},
            )

        task_id = await self._open_task(job, lane=lane, ended=ended)

        # 3. STEP — the narrative. Once one of two legs here; now the whole job.
        mined = 0
        try:
            raw = await self._complete(transcript)
        except Exception as exc:
            log.memory.error(
                "[memory] rollover_summary.execute: summary call failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "tier": self._tier,
                                   "mined": mined}},
            )
            await self._close_task(task_id, status="failed",
                                   result=f"summary call failed: {exc}")
            return self._failed(job, t0, f"summary call failed: {exc}", mined=mined)

        parsed = _parse_summary(raw)
        staged = False
        if parsed is None:
            log.memory.warning(
                "[memory] rollover_summary.execute: unparseable summary — storing nothing",
                extra={"_fields": {"job_id": job.job_id,
                                   "raw_preview": (raw or "")[:200]}},
            )
        elif parsed:
            staged = await self._stage(parsed, scope=scope, ended=ended)
        else:
            log.memory.info(
                "[memory] rollover_summary.execute: nothing notable — storing nothing",
                extra={"_fields": {"job_id": job.job_id, "ended_conversation_id": ended}},
            )

        await self._close_task(
            task_id, status="completed",
            result=f"mined={mined} summary_staged={staged}",
        )
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.memory.info(
            "[memory] rollover_summary.execute: exit",
            extra={"_fields": {"job_id": job.job_id, "session_key": lane,
                               "mined": mined, "summary_staged": staged,
                               "messages": len(transcript),
                               "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=True,
            output=f"mined={mined} summary_staged={staged}", error=None,
            duration_ms=duration_ms,
            metadata={"mined": mined, "summary_staged": staged,
                      "summary_parsed": parsed is not None,
                      "messages": len(transcript)},
        )

    # ------------------------------------------------------------------ steps

    async def _read_transcript(self, ended_conversation_id: str) -> list[Message]:
        """The WHOLE ended incarnation, up to a safety ceiling.

        Keyed on the incarnation rather than the lane: a ``conversations`` row IS
        one incarnation (its id is the conversation_id), so this cannot accidentally
        summarise a previous run of the same conversation.
        """
        rows = await self._db.fetch_all(
            _TRANSCRIPT_SQL,
            (ended_conversation_id, ended_conversation_id, self._max_messages),
        )
        out: list[Message] = []
        for row in rows:
            role = row["role"]
            if role not in ("user", "assistant"):
                continue
            out.append(Message(role=role, content=row["content"]))
        log.memory.debug(
            "[memory] rollover_summary._read_transcript: exit",
            extra={"_fields": {"ended_conversation_id": ended_conversation_id,
                               "messages": len(out), "capped": len(rows)
                               >= self._max_messages}},
        )
        return out

    async def _complete(self, transcript: list[Message]) -> str:
        provider, model = self._providers.get_with_cascade(self._tier)  # type: ignore[attr-defined]
        body = "\n".join(f"{m.role}: {m.content}" for m in transcript)
        prompt = self._env.get_template(_TEMPLATE_NAME).render(conversation_text=body)
        log.memory.debug(
            "[memory] rollover_summary._complete: request",
            extra={"_fields": {"tier": self._tier, "model": model,
                               "prompt_chars": len(prompt),
                               "messages": len(transcript)}},
        )
        result = await provider.complete([Message(role="user", content=prompt)],
                                         model=model)
        return str(getattr(result, "content", "") or "")

    async def _stage(self, summary: str, *, scope: str, ended: str) -> bool:
        fact = StagedFact(
            content=summary,
            source_type=SUMMARY_SOURCE_TYPE,
            # The PERSON, so recall finds it. Never the owl-prefixed lane.
            source_ref=scope,
            confidence=SUMMARY_CONFIDENCE,
            # Self-authored: we wrote this from our own transcript, not from
            # merged external content.
            trust="self",
        )
        try:
            await self._bridge.stage(fact)  # type: ignore[attr-defined]
        except Exception as exc:
            log.memory.error(
                "[memory] rollover_summary._stage: staging failed — summary lost",
                exc_info=exc,
                extra={"_fields": {"scope": scope, "ended_conversation_id": ended}},
            )
            return False
        log.memory.info(
            "[memory] rollover_summary._stage: summary staged",
            extra={"_fields": {"scope": scope, "ended_conversation_id": ended,
                               "fact_id": fact.fact_id, "chars": len(summary)}},
        )
        return True

    # ------------------------------------------------- the durable task record

    async def _open_task(self, job: Job, *, lane: str, ended: str) -> str | None:
        """Record the boundary as a durable task (Bakir's Q15, reaffirmed).

        Used as a CHECKPOINT RECORD driven by this handler, not as a goal fed to
        the ReAct kernel: the kernel drives an agent loop toward an objective, and
        this is one call. Best-effort — a bookkeeping row must never be the reason
        a conversation is not summarised.
        """
        task_id = f"rollover-{uuid.uuid4().hex[:12]}"
        try:
            # THE PRINCIPAL OWNS THE WORK; THE SCOPE OWNS THE KNOWLEDGE. `owner`
            # here is `owner_scope_key` — identity_key or the lane — which answers
            # "who is this SUMMARY about". `tasks.owner_id` answers a different
            # question: which principal owns this WORK. It is the tenancy boundary
            # the ONE loop, the liveness sweep and every _require_owned check turn
            # on, and `TaskLoop` is constructed with no owner, so it claims
            # `principal-default` and nothing else.
            #
            # MEASURED 2026-08-20: filing the row under the scope had put 387 rows
            # beyond the loop's reach — 72 of them `pending`, the oldest a day and
            # a half old and every one past its next_attempt_at. Work in a table
            # nobody is draining, which loop.py's own docstring calls worse than
            # having no loop. Every non-principal row in the table was written
            # here; every other writer was already correct.
            #
            # The scope is still what `_stage` files the summary under, which is
            # right: knowledge is about a person, work belongs to a tenant.
            store = DurableTaskStore(self._db, DEFAULT_PRINCIPAL_ID)
            now = datetime.now(UTC)
            await store.create(DurableTask(
                task_id=task_id, owner_id=DEFAULT_PRINCIPAL_ID,
                goal=f"rollover summary for {lane} incarnation {ended}",
                status="running", created_at=now, updated_at=now,
            ))
            return task_id
        except Exception as exc:
            log.memory.error(
                "[memory] rollover_summary._open_task: could not record the task — "
                "continuing without a checkpoint",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id, "lane": lane}},
            )
            return None

    async def _checkpoint(self, task_id: str | None, *, blob: str) -> None:
        if task_id is None:
            return
        try:
            # Same principal the row was created under — see _open_task. A
            # checkpoint written under a different owner reaches no row at all.
            await DurableTaskStore(self._db, DEFAULT_PRINCIPAL_ID).save_checkpoint(
                task_id, blob,
            )
        except Exception as exc:
            log.memory.warning(
                "[memory] rollover_summary._checkpoint: not saved",
                exc_info=exc, extra={"_fields": {"task_id": task_id}},
            )

    async def _close_task(self, task_id: str | None, *, status: str,
                          result: str) -> None:
        """Terminalise the record. A row left 'running' for ever is the zombie
        this record exists to prevent."""
        if task_id is None:
            return
        try:
            # Same principal the row was created under — see _open_task.
            await DurableTaskStore(self._db, DEFAULT_PRINCIPAL_ID).update_status(
                task_id, status, result=result,  # type: ignore[arg-type]
            )
        except Exception as exc:
            log.memory.warning(
                "[memory] rollover_summary._close_task: status not written",
                exc_info=exc,
                extra={"_fields": {"task_id": task_id, "status": status}},
            )

    # ---------------------------------------------------------------- helpers

    def _failed(self, job: Job, t0: float, error: str, *, mined: int) -> JobResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.memory.error(
            "[memory] rollover_summary.execute: exit — failed",
            extra={"_fields": {"job_id": job.job_id, "error": error,
                               "mined": mined, "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=False,
            output=None, error=error, duration_ms=duration_ms,
            metadata={"mined": mined, "summary_staged": False,
                      "summary_parsed": False},
        )


async def enqueue_rollover_summary(
    db: object, *, lane: str, ended: str,
    identity_key: object = None, owl_name: object = None, channel: object = None,
    reason: object = None, message_count: object = None,
    completed_turns: object = None,
) -> bool:
    """Queue one durable summary job for one boundary. Returns whether it queued.

    ONE enqueue implementation, TWO triggers — the live event and the five-minute
    backstop — for the same reason ``/new`` and the daily boundary share one code
    path: two copies would drift into behaving differently, and this one is the
    step that makes Q15's durability real.

    Idempotency is the DATABASE's. ``jobs.idempotency_key`` is UNIQUE and the key is
    ``rollover:{lane}:{ended_incarnation}``, so one boundary yields one summary
    however many times it is announced or recovered. That is what lets the backstop
    be unconditional instead of clever.
    """
    job = Job(
        job_id=f"{HANDLER_NAME}-{uuid.uuid4().hex[:8]}",
        handler_name=HANDLER_NAME,
        schedule="manual",
        idempotency_key=f"rollover:{lane}:{ended}",
        last_run_at=None,
        # Due immediately: the scheduler picks it up on its next poll.
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        params={
            "session_key": lane,
            "ended_conversation_id": ended,
            "identity_key": identity_key,
            "owl_name": owl_name,
            "channel": channel,
            "reason": reason,
            "message_count": message_count,
            "completed_turns": completed_turns,
            # The scheduler decides recurring-vs-one-shot from this flag
            # (scheduler._is_recurring). Without it a boundary's job would re-arm
            # onto a cadence for ever and re-summarise a conversation that ended
            # once.
            "run_once": True,
        },
    )
    try:
        from stackowl.scheduler.scheduler_helpers import insert_job

        await insert_job(db, job)  # type: ignore[arg-type]
    except Exception as exc:
        # A UNIQUE violation here is the EXPECTED outcome of a re-announced or
        # re-recovered boundary, not a fault; anything else is. Both are logged,
        # because telling them apart by exception text would be guessing at a
        # driver's message, and neither may break the conversation.
        log.memory.info(
            "[memory] rollover_summary.enqueue: not queued (already queued, or the "
            "queue refused it) — the boundary itself is unaffected",
            exc_info=exc,
            extra={"_fields": {"session_key": lane, "ended_conversation_id": ended,
                               "idempotency_key": job.idempotency_key}},
        )
        return False
    log.memory.info(
        "[memory] rollover_summary.enqueue: queued",
        extra={"_fields": {"session_key": lane, "ended_conversation_id": ended,
                           "job_id": job.job_id, "reason": reason}},
    )
    return True


def register_rollover_consumer(event_bus: object, db: object,
                               store: object | None = None) -> None:
    """Turn a ``session.rollover`` announcement into DURABLE work.

    Bakir's Q15: a rollover fires at 4 AM unattended, which is exactly when nobody
    is watching, so the consumer must ENQUEUE rather than work inline — anything
    done in the handler thread is lost if the process dies mid-summary.

    Idempotency is the DATABASE's job, not the event's. ``jobs.idempotency_key`` is
    UNIQUE and the key here is ``rollover:{lane}:{ended_incarnation}``, so one
    boundary produces one summary however many times it is announced. That matters
    concretely: the sweeper finalises a lane on the clock and the next inbound
    message resolves it, and a double-announce guard already exists in the store —
    this is the second belt, enforced by the schema rather than by agreement.

    A consumer that cannot enqueue must never break the boundary. The conversation
    starting is more important than the summary, so every failure here is logged
    and swallowed.
    """
    from stackowl.sessions.store import SessionStore

    async def _on_rollover(payload: dict[str, Any] | None) -> None:
        data = payload or {}
        lane = str(data.get("session_key") or "")
        ended = str(data.get("old_conversation_id") or "")
        # No ended incarnation means nothing finished, so there is nothing to
        # summarise. The sweeper legitimately publishes new_conversation_id=None (it
        # finalises without minting) — that is fine; a missing OLD id is not.
        if not lane or not ended:
            log.memory.debug(
                "[memory] rollover_consumer: nothing ended — not enqueueing",
                extra={"_fields": {"session_key": lane, "old_conversation_id": ended}},
            )
            return
        queued = await enqueue_rollover_summary(
            db,
            lane=lane,
            ended=ended,
            identity_key=data.get("identity_key"),
            owl_name=data.get("owl_name"),
            channel=data.get("channel"),
            reason=data.get("reason"),
            message_count=data.get("message_count"),
            completed_turns=data.get("completed_turns"),
        )
        # Record it so the five-minute backstop does not queue the same boundary a
        # second time. Only on success: a failed enqueue must stay recoverable.
        if queued and store is not None:
            try:
                await store.mark_summary_enqueued(lane, ended)  # type: ignore[attr-defined]
            except Exception as exc:
                log.memory.warning(
                    "[memory] rollover_consumer: could not record the enqueue — the "
                    "backstop may queue it again, which the UNIQUE key absorbs",
                    exc_info=exc,
                    extra={"_fields": {"session_key": lane, "ended_conversation_id": ended}},
                )

    event_bus.subscribe(SessionStore.ROLLOVER_EVENT, _on_rollover)  # type: ignore[attr-defined]
    log.memory.info(
        "[memory] rollover_consumer: subscribed",
        extra={"_fields": {"event": SessionStore.ROLLOVER_EVENT,
                           "handler": HANDLER_NAME}},
    )


def _parse_summary(raw: str) -> str | None:
    """Return the summary text, ``""`` for "nothing notable", ``None`` if unusable.

    The notability decision is carried in a STRUCTURED field, never sniffed out of
    prose: matching phrases would be an English keyword list, and this prompt asks
    the model to write in the conversation's own language.
    """
    text = _FENCE_CLOSE_RE.sub("", _FENCE_OPEN_RE.sub("", (raw or "").strip()))
    if not text:
        return None
    try:
        payload: Any = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or "notable" not in payload:
        return None
    if not payload.get("notable"):
        return ""
    summary = str(payload.get("summary") or "").strip()
    # notable=true with nothing in it is a contradiction, not a summary.
    return summary or None
