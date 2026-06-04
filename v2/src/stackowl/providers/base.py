"""ModelProvider ABC — common interface for all AI provider implementations."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.providers.react_callback import IterationCallback

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.providers.cost_tracker import CostTracker


class DocumentBlock(BaseModel):
    """A binary document (e.g. a PDF) attached to a Message for native handling.

    Carries the raw bytes plus a MIME type so a document-capable provider can ship
    the file to a vision/document model. Text-only providers ignore it (they read
    ``Message.content`` only) — see ``ModelProvider.supports_document``, which
    defaults False so existing providers report "not document-capable" rather than
    silently dropping the attachment. Added for the ``pdf`` tool's Mode B (E3-S4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    data: bytes
    media_type: str = "application/pdf"
    filename: str | None = None


class Message(BaseModel):
    """A single conversation turn.

    ``content`` (text) is the load-bearing field every provider reads. ``documents``
    is an optional, default-empty attachment list used only by document-capable
    providers (E3-S4 Mode B); text-only providers leave it untouched, so adding it
    is fully backward-compatible — all existing call sites construct a Message with
    only ``role``/``content`` and keep working.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    documents: tuple[DocumentBlock, ...] = ()


class CompletionResult(BaseModel):
    """The output of a non-streaming provider completion call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider_name: str
    duration_ms: float


class ModelProvider(ABC):
    """Abstract interface for all AI provider backends.

    Concrete classes: AnthropicProvider, OpenAIProvider, GeminiProvider, MockProvider.
    ProviderRegistry holds only ModelProvider references — no concrete class knowledge
    outside the providers package.
    """

    # E8-S0cost — the ONE shared CostTracker, injected by ProviderRegistry after
    # construction (set_cost_tracker). When present, every LLM call this provider
    # makes (complete + each complete_with_tools API round) records its usage so a
    # turn's REAL spend feeds CostTracker.turn_cost_usd(trace_id) and the soft
    # cost-pause can fire. None by default (tests / standalone providers) → the
    # recording helper is a no-op. NEVER let recording break a completion (B5).
    _cost_tracker: CostTracker | None = None

    def set_cost_tracker(self, cost_tracker: CostTracker | None) -> None:
        """Inject the shared CostTracker (idempotent; ProviderRegistry calls this)."""
        self._cost_tracker = cost_tracker

    async def _record_cost(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float,
    ) -> None:
        """Record ONE LLM call's cost to the shared tracker (single recording site).

        Reads ``trace_id`` off :class:`TraceContext` so the parent turn, its
        delegated children, and any sub-pipeline all fold into the SAME per-turn
        running total (the context propagates the id across async hops). Best-effort
        and self-healing (B5): no tracker wired, or any error (including the daily
        hard-cap raise the NEXT call would trigger) is logged and swallowed here so
        cost accounting can NEVER break or block a completion that already happened.
        """
        tracker = self._cost_tracker
        if tracker is None:
            return
        trace_id = str(TraceContext.get().get("trace_id") or "")
        try:
            await tracker.record(
                provider_name=self.name,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                trace_id=trace_id,
            )
        except Exception as exc:  # B5 — never let cost recording break a completion.
            log.engine.error(
                "[provider] _record_cost: cost record failed — continuing",
                exc_info=exc,
                extra={"_fields": {"provider": self.name, "model": model}},
            )

    @property
    @abstractmethod
    def name(self) -> str:
        """Logical name of this provider (from ProviderConfig.name)."""
        ...

    @property
    @abstractmethod
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        """Wire protocol this provider speaks."""
        ...

    @property
    def supports_document(self) -> bool:
        """Whether this provider can accept ``Message.documents`` (native document blocks).

        Defaults to **False** on the ABC so EVERY existing provider is unchanged and
        reports "text-only" — a caller routing a document (pdf Mode B, E3-S4) checks
        this flag and returns "needs a document-capable model" rather than letting a
        text-only provider silently drop the bytes. A provider backed by a
        vision/document-capable model overrides this to return True.
        """
        return False

    @property
    def supports_vision(self) -> bool:
        """Whether this provider's configured model can accept IMAGE blocks (E10-S1).

        An image is carried as a :class:`DocumentBlock` whose ``media_type`` is
        ``image/*`` (see ``providers._blocks``). Defaults **False** on the ABC so a
        caller routing an image (the vision selector) checks this flag and reports
        "no vision backend" rather than letting a text-only provider drop the
        bytes. A provider whose ``default_model`` is a known vision/multimodal model
        overrides this to True (see ``providers.vision_models.is_vision_model``).
        """
        return False

    @abstractmethod
    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        """Run a non-streaming completion and return the full result."""
        ...

    @abstractmethod
    def stream(self, messages: list[Message], model: str, **kwargs: object) -> AsyncIterator[str]:
        """Yield text deltas as they arrive from the provider."""
        ...

    async def complete_with_tools(
        self,
        user_text: str,
        system_text: str | None,
        tool_schemas: list[dict[str, Any]],
        tool_dispatcher: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_iterations: int = 8,
        history: list[Message] | None = None,
        persistence_check: Callable[[str, list[str]], Awaitable[str | None]] | None = None,
        on_iteration_complete: IterationCallback | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
        resume_tool_calls: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run a multi-turn tool loop; return (final_response_text, tool_invocation_records).

        ``persistence_check`` (Phase D) is an optional real-time deliver-vs-giveup
        hook: a provider that supports the tool loop calls it just BEFORE returning
        a final answer with ``(draft_answer, tool_names_used)``; if it returns a
        non-empty directive string the provider must inject it and CONTINUE the loop
        (bounded, fail-open) instead of returning. The default impl below ignores
        tools entirely, so it ignores this hook too.

        ``on_iteration_complete`` (S3) is an optional per-iteration callback fired
        once at the bottom of each completed ReAct iteration (after that iteration's
        tool calls + observations have been appended to messages).  Default ``None``
        → exact current behavior; no loop, no I/O.  The base default below has no
        loop so this callback is never invoked here; concrete providers with a real
        ReAct loop call it at each iteration bottom.  If the callback raises the
        exception propagates — providers do NOT swallow it.

        ``resume_messages`` (B1) is the durable-ReAct resume seam.  When ``None``
        (the default, always today's behavior) the loop builds its initial
        ``messages`` list from ``user_text`` + ``system_text`` + ``history``
        exactly as before — byte-for-byte unchanged.  When provided it is a
        pre-built mid-loop transcript (a :class:`ReActCheckpoint`\'s ``messages``
        field) that is used DIRECTLY as the loop's starting ``messages`` list;
        ``user_text``, ``system_text``, and ``history`` are NOT re-injected so the
        checkpoint is restored without duplication.  For OpenAI the system prompt
        is already embedded at ``resume_messages[0]``; for Anthropic the system
        prompt is always in ``system_kwargs`` (separate from the messages list) so
        ``resume_messages`` contains only conversation turns.

        ``resume_tool_calls`` (B1 hardening) is the companion of ``resume_messages``:
        the ``tool_call_records`` accumulated BEFORE the crash (a
        :class:`ReActCheckpoint`\'s ``tool_call_records`` field).  When ``None``
        (default) the loop's ``all_calls`` accumulator starts empty as before.
        When provided it SEEDS ``all_calls`` so the returned records, the
        persistence give-up judge (which reads ``summarize_tool_outcomes(all_calls)``),
        and the per-iteration callback all see the FULL prior-plus-new tool history
        instead of only the post-resume calls.  Pass it together with
        ``resume_messages`` on resume.

        Default: falls back to a single complete() ignoring tools.
        Providers that support tool use override this method.
        """
        msgs: list[Message] = []
        if system_text:
            msgs.append(Message(role="system", content=system_text))
        msgs.extend(history or [])
        msgs.append(Message(role="user", content=user_text))
        result = await self.complete(msgs, model="")
        return result.content, []

    # ---- HealableResource protocol --------------------------------------
    # Providers are stateless wrappers around remote HTTP APIs. Per-call
    # transient failure is handled by the SDK's built-in retry (anthropic/openai
    # SDKs auto-retry connection errors & 5xx). Persistent failure is handled
    # by the per-provider CircuitBreaker in ``providers.registry`` which auto
    # transitions OPEN → HALF_OPEN → CLOSED. The protocol surface here is
    # always "available"; subclasses may override.

    @property
    def available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str | None:
        return None

    async def ensure_available(self) -> None:
        """No-op: providers are stateless. Recovery happens via CircuitBreaker."""
        return

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """No-op: providers don't recycle (no long-lived handle)."""
        log.engine.debug(
            "[provider] register_on_recycled: no-op (stateless provider)",
            extra={"_fields": {"provider": self.name}},
        )

    async def health_check(self) -> HealthStatus:
        """Default lightweight health probe — subclasses may override."""
        t0 = time.monotonic()
        try:
            await self.complete(
                [Message(role="user", content="ping")],
                model="",
                max_tokens=1,
            )
            return HealthStatus(
                name=self.name,
                status="ok",
                message=None,
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return HealthStatus(
                name=self.name,
                status="degraded",
                message=str(exc),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
