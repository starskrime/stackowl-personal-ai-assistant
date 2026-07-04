"""Progress emitter — observes the ReAct loop and publishes live status.

A single emitter, composed onto the provider's ``on_iteration_complete`` seam,
turns each ReAct boundary into ONE normalized progress fact and fans it out to:
  * the EventBus as ``pipeline_step_changed`` → the terminal ``PipelineStrip``;
  * (Phase 2) a ``kind="progress"`` ``ResponseChunk`` on the turn's stream → the
    Telegram mutating status message.

It is OBSERVE-ONLY: the iteration callback always returns ``None`` so it never
perturbs steering/budget folding, and every emit is best-effort (a failure is
logged and swallowed — progress must never break a turn).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log
from stackowl.pipeline.progress import vocabulary
from stackowl.pipeline.progress.vocabulary import ProgressKey
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.react_callback import IterationCallback, ReActIterationState

if TYPE_CHECKING:
    from stackowl.pipeline.services import StepServices as PipelineServices
    from stackowl.pipeline.state import PipelineState

PIPELINE_STEP_EVENT = "pipeline_step_changed"

# Nominal step budget for the strip's "train"; the loop length is unknown ahead
# of time, so this is a presentational hint, not a hard bound.
_NOMINAL_TOTAL_STEPS = 8


def is_eligible(state: PipelineState, services: PipelineServices) -> bool:
    """Decide whether THIS turn may surface live progress.

    Mirrors deliver.py's gating (no delegated child, no deferred delivery) plus
    interactivity + a resolvable target + the master flag. Returns False ⇒ the
    caller composes NO progress callback, so the provider call is byte-identical.
    """
    settings = getattr(services, "settings", None)
    progress = getattr(settings, "progress", None) if settings is not None else None
    if progress is None or not getattr(progress, "live_progress", False):
        return False
    if state.delegation_depth != 0:
        return False  # delegated child has no live user stream
    if state.defer_delivery:
        return False  # non-interactive producer; deliver sends nothing
    if not state.interactive:
        return False  # cron/scheduler/parliament — no human waiting
    # CLI is a single terminal (reply_target is None by design); other channels
    # need a concrete target to reach the right chat.
    return not (state.reply_target is None and state.channel != "cli")


class _ProgressEmitter:
    """Per-turn progress state machine (closure helper)."""

    def __init__(self, state: PipelineState, services: PipelineServices) -> None:
        self._state = state
        self._services = services
        self._lang = state.language or "en"
        self._seen_records = 0
        self._step_index = 0

    async def on_iteration(self, s: ReActIterationState) -> None:
        """Translate one completed ReAct iteration into a progress emit."""
        try:
            records = s.tool_call_records or []
            if len(records) > self._seen_records:
                new = records[self._seen_records:]
                self._seen_records = len(records)
                last = new[-1]
                if last.get("failed"):
                    text = vocabulary.render(ProgressKey.RECOVER, self._lang)
                else:
                    text = self._render_tool(last)
            else:
                # No new tool calls → the model produced text; the answer is near.
                text = vocabulary.render(ProgressKey.SYNTH, self._lang)
            await self._emit(text)
        except Exception as exc:  # noqa: BLE001 — progress is best-effort
            log.engine.warning(
                "[progress] emitter.on_iteration: emit failed — continuing",
                exc_info=exc,
                extra={"_fields": {"trace_id": self._state.trace_id}},
            )

    def _render_tool(self, record: dict[str, Any]) -> str:
        key = self._key_for_tool(record.get("name"))
        skill = None
        if key is ProgressKey.SKILL_RUN:
            args = record.get("args") or {}
            if isinstance(args, dict):
                raw = args.get("skill") or args.get("skill_name") or args.get("name")
                skill = str(raw) if raw else None
        return vocabulary.render(key, self._lang, skill=skill)

    def _key_for_tool(self, name: object) -> ProgressKey:
        registry = getattr(self._services, "tool_registry", None)
        if registry is None or not isinstance(name, str):
            return ProgressKey.THINK
        tool = registry.get(name)
        if tool is None:
            return ProgressKey.THINK
        return vocabulary.coerce_key(tool.manifest.progress_key)

    async def emit_start(self) -> None:
        """One-shot 'Working on it…' before the first model round."""
        try:
            await self._emit(vocabulary.render(ProgressKey.ACK, self._lang))
        except Exception as exc:  # noqa: BLE001
            log.engine.warning(
                "[progress] emitter.emit_start: failed — continuing",
                exc_info=exc,
                extra={"_fields": {"trace_id": self._state.trace_id}},
            )

    async def _emit(self, text: str) -> None:
        self._step_index += 1
        # (1) EventBus → terminal PipelineStrip (TUI-only consumer).
        bus = getattr(self._services, "event_bus", None)
        if bus is not None:
            bus.emit(
                PIPELINE_STEP_EVENT,
                {
                    "step_name": text,
                    "step_index": self._step_index,
                    "total_steps": _NOMINAL_TOTAL_STEPS,
                },
            )
        # (2) Progress ResponseChunk on the turn's stream → Telegram (which has no
        # EventBus consumer). Best-effort: a missing writer / write error never
        # breaks the turn. The CLI adapter SKIPS kind="progress" chunks (it gets
        # liveness from the EventBus path above).
        await self._write_progress_chunk(text)
        log.engine.debug(
            "[progress] emit",
            extra={"_fields": {
                "trace_id": self._state.trace_id,
                "step_index": self._step_index,
                "text": text,
            }},
        )

    async def _write_progress_chunk(self, text: str) -> None:
        registry = getattr(self._services, "stream_registry", None)
        if registry is None:
            return
        writer = registry.get_writer(self._state.trace_id)
        if writer is None:
            return  # live reader gone (stream-miss) — progress is best-effort
        await writer.write(ResponseChunk(
            content=text,
            is_final=False,  # progress is NEVER terminal; close() is the lone sentinel
            chunk_index=-2,  # sentinel-ish index; adapters key on `kind`, not index
            trace_id=self._state.trace_id,
            owl_name=self._state.owl_name,
            target=self._state.reply_target,  # emitter stamps its own target
            kind="progress",
        ))


def make_progress_callback(
    state: PipelineState, services: PipelineServices
) -> IterationCallback | None:
    """Build the observe-only progress callback, or None when this turn is gated.

    Returning None keeps the composed callback list (and thus the provider call)
    byte-identical to the no-progress baseline.
    """
    if not is_eligible(state, services):
        return None
    emitter = _ProgressEmitter(state, services)

    async def _cb(s: ReActIterationState) -> None:
        await emitter.on_iteration(s)
        return None

    # Stash the emitter so the caller can fire the one-shot start fact.
    _cb._emitter = emitter  # type: ignore[attr-defined]
    return _cb


async def emit_start(callback: IterationCallback | None) -> None:
    """Fire the one-shot 'Working on it…' for a callback from make_progress_callback."""
    emitter = getattr(callback, "_emitter", None)
    if emitter is not None:
        await emitter.emit_start()


# Turn-scoped carrier for THIS turn's progress callback (Task 2). Mirrors the
# TraceContext/lesson_context ContextVar idiom: asyncio_backend.py binds the
# callback it built for the pre-loop ack, and execute.py's tool loop reuses
# THAT SAME callback/emitter (same step_index counter) instead of building a
# second, independent one — which previously made the PipelineStrip's glyph
# "train" (see tui/widgets/pipeline_strip.py) stall for one step right after
# the ack. bind()/reset() in a try/finally keeps it from leaking across turns
# or across concurrent turns (each turn runs in its own asyncio Task).
_turn_callback: ContextVar[IterationCallback | None] = ContextVar(
    "progress_turn_callback", default=None,
)


def bind_turn_callback(callback: IterationCallback | None) -> Token[IterationCallback | None]:
    """Bind THIS turn's progress callback (may be None when the turn is gated)."""
    return _turn_callback.set(callback)


def reset_turn_callback(token: Token[IterationCallback | None]) -> None:
    _turn_callback.reset(token)


def get_turn_callback() -> IterationCallback | None:
    """The callback bound by ``bind_turn_callback`` for the CURRENT turn, if any."""
    return _turn_callback.get()
