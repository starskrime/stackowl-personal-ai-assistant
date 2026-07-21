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
    from stackowl.providers.circuit_breaker import CircuitBreaker
    from stackowl.providers.cost_tracker import CostTracker
    from stackowl.providers.rate_limiter import RateLimiter


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

    # C2/F115 — the registry-owned CircuitBreaker + RateLimiter the cascade READS,
    # injected after construction (set_resilience) exactly like _cost_tracker. The
    # provider WRITES breaker state at its per-round HTTP boundary via
    # resilient_round so a genuinely-down provider is actually skipped by the
    # cascade next turn. None by default (tests / standalone providers) → the
    # per-round bracket is a byte-identical pass-through.
    _breaker: CircuitBreaker | None = None
    _limiter: RateLimiter | None = None
    # F-quota — this provider's configured cooldown_hours (ProviderConfig),
    # injected by ProviderRegistry alongside breaker/limiter. None (default)
    # → the RATE_LIMIT branch in _resilient_round has no config fallback.
    _cooldown_hours: float | None = None

    def set_cost_tracker(self, cost_tracker: CostTracker | None) -> None:
        """Inject the shared CostTracker (idempotent; ProviderRegistry calls this)."""
        self._cost_tracker = cost_tracker

    def set_resilience(
        self,
        breaker: CircuitBreaker | None,
        limiter: RateLimiter | None,
    ) -> None:
        """Inject the registry-owned breaker + limiter (SP-4; idempotent).

        The SAME breaker object the cascade reads next turn (registry-owned,
        name-keyed) so a recorded fault drives selection. Default no-op-safe: when
        both are None the provider's ``_resilient_round`` is a pass-through, keeping
        a standalone/test provider byte-identical.
        """
        self._breaker = breaker
        self._limiter = limiter

    def set_cooldown_hours(self, hours: float | None) -> None:
        """Inject this provider's configured cooldown_hours (idempotent)."""
        self._cooldown_hours = hours

    async def _resilient_round[T](
        self,
        do_round: Callable[[], Awaitable[T]],
    ) -> T:
        """Run ONE remote round through the shared breaker+limiter site (SP-2).

        Thin instance bracket over :func:`providers._resilient_round.resilient_round`
        binding this provider's injected breaker/limiter/cooldown_hours. Concrete
        providers wrap EVERY remote round (each tool-loop ``create()``, the
        wrap-up round, the ``complete()``/``stream()`` round) in this so
        breaker-record + limiter-acquire + quota-cooldown share ONE audited site.
        Pass-through when nothing is injected.
        """
        from stackowl.providers._resilient_round import resilient_round

        return await resilient_round(
            self._breaker, self._limiter, do_round, cooldown_hours=self._cooldown_hours,
        )

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
                is_local=self._is_local_backend,
            )
        except Exception as exc:  # B5 — never let cost recording break a completion.
            log.engine.error(
                "[provider] _record_cost: cost record failed — continuing",
                exc_info=exc,
                extra={"_fields": {"provider": self.name, "model": model}},
            )

    @property
    def _is_local_backend(self) -> bool:
        """Whether this provider's backend is self-hosted (on-box / private net).

        Drives locality-aware pricing (F128): an unknown model on a LOCAL backend
        stays $0; an unknown CLOUD model gets a conservative fallback price.
        Defaults **False** (cloud) on the ABC — the conservative, fail-safe-to-paid
        default. The only backend that can be local is an openai-compatible one
        pointed at a loopback/private base_url (e.g. Ollama), which overrides this.
        """
        return False

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
    def supports_tools(self) -> bool:
        """Whether this provider can run the agentic tool-use loop (F120).

        Defaults **True** on the ABC: openai/anthropic providers override
        ``complete_with_tools`` with a real ReAct loop. A provider that only
        defines ``complete``/``stream`` (currently :class:`GeminiProvider`) inherits
        the base default ``complete_with_tools`` — which IGNORES ``tool_schemas``
        and returns ``(content, [])``, a silently non-agentic "can't act" reply the
        give-up judge may pass as delivered. Such a provider overrides this to
        **False** so the selector skips it for an agentic turn (loud route-away or
        honest floor) instead of silently degrading. Mirrors
        ``supports_vision``/``supports_document``.
        """
        return True

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
        model: str = "",
        max_iterations: int = 8,
        history: list[Message] | None = None,
        persistence_check: Callable[[str, list[str]], Awaitable[str | None]] | None = None,
        on_iteration_complete: IterationCallback | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
        resume_tool_calls: list[dict[str, Any]] | None = None,
        wrapup_deadline_s: float | None = None,
        can_escalate: bool = False,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run a multi-turn tool loop; return (final_response_text, tool_invocation_records).

        ``can_escalate`` (set by :class:`LLMGateway` below the ceiling tier) lets a
        tool-capable provider return the ESCALATE sentinel — instead of leaking a
        raw tool call or flooring — when it persistently cannot act, so the gateway
        re-runs the loop on a stronger tier. Default ``False`` ⇒ unchanged behaviour.
        The base default ignores it (it cannot run tools at all).

        ``wrapup_deadline_s`` (F027/SP-4) is the residual wall-clock budget the
        execute step (the BudgetGovernor owner) computes for the terminal Phase-F
        wrap-up call. When set, a concrete provider wraps its terminal ``create``
        in ``asyncio.wait_for(..., timeout=wrapup_deadline_s)`` so a hung wrap-up is
        bounded; on ``TimeoutError`` it routes to the existing fail-open floor.
        ``None`` (the default, always today) → byte-identical to current behavior.
        The provider receives a VALUE, never the governor object.

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

        ``model`` (Task 22) is the resolved per-model override the caller (tier
        resolution / ``LLMGateway``) wants THIS call to run on — mirrors the
        same ``model or self._config.default_model`` fallback already used by
        ``complete()``/``stream()``. Default ``""`` (falsy) preserves today's
        exact behavior: every call site the base default forwards it to falls
        back to the provider's ``default_model`` unchanged.

        Default: falls back to a single complete() ignoring tools.
        Providers that support tool use override this method.
        """
        # F120 defense-in-depth: a non-empty tool_schemas reaching this base default
        # means a non-tool-capable provider (supports_tools is False) was routed an
        # agentic turn despite the selector gate. Fail LOUD with a typed error rather
        # than silently returning (content, []) — a fake-success the judge may pass.
        if tool_schemas:
            from stackowl.exceptions import ToolUseUnsupportedError

            log.engine.error(
                "[provider] complete_with_tools: base default reached with tools — "
                "this provider cannot act; refusing to silently degrade",
                extra={"_fields": {"provider": self.name, "tool_count": len(tool_schemas)}},
            )
            raise ToolUseUnsupportedError(self.name)
        msgs: list[Message] = []
        if system_text:
            msgs.append(Message(role="system", content=system_text))
        msgs.extend(history or [])
        msgs.append(Message(role="user", content=user_text))
        result = await self.complete(msgs, model=model)
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
