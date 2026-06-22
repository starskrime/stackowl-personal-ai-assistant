"""LLMGateway — the single escalating LLM interface.

Every LLM consumer goes through this instead of obtaining a provider from the
registry and invoking it directly. A request starts at the ``floor`` tier
(default ``"fast"``) and escalates ``fast → standard → powerful`` — bounded by
``ceiling`` — whenever the model itself signals the task is beyond it via the
in-band ``ESCALATE`` sentinel.

This adds **complexity-based** escalation. The registry's existing
**failure-based** fallback (skip a provider whose circuit is OPEN) still runs
UNDERNEATH via ``resolve_tier_with_fallback`` — the two compose: escalation picks
the target tier, failure-fallback finds a live provider for it.

Meta-calls that must not recurse (the intent router, the give-up judges) pin
``floor == ceiling`` to a single tier: same uniform interface, no escalation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log
from stackowl.providers.base import CompletionResult, Message

if TYPE_CHECKING:
    from stackowl.providers.registry import ProviderRegistry

# The escalation ladder, least → most capable. ``local`` is a separate axis
# (offline backend), NOT part of the quality ladder.
LADDER: tuple[str, ...] = ("fast", "standard", "powerful")

ESCALATE_SENTINEL = "ESCALATE"

# Appended to the system prompt for any attempt below the ceiling. Kept short and
# unambiguous; the model emits the bare sentinel ONLY when it truly cannot answer.
ESCALATE_INSTRUCTION = (
    "\n\nIf this request is genuinely beyond your capability to answer well — it "
    "needs deeper reasoning, broader knowledge, or more skill than you have — reply "
    "with EXACTLY the single word ESCALATE and nothing else. It will be routed to a "
    "more capable model. Only do this when you truly cannot produce a good answer; "
    "otherwise answer normally."
)


def is_escalate_signal(text: str | None) -> bool:
    """True when a model response is the bare escalation sentinel (tolerant of
    surrounding whitespace / trailing punctuation, case-insensitive)."""
    if not text:
        return False
    cleaned = text.strip().strip(".!?\"'` ").upper()
    return cleaned == ESCALATE_SENTINEL


def tier_span(floor: str, ceiling: str) -> list[str]:
    """The ladder slice [floor..ceiling] inclusive (clamped, order-safe).

    Unknown tiers fall back to the full ladder bound so a typo never silently
    yields an empty span (which would mean "never call a model").
    """
    try:
        lo = LADDER.index(floor)
    except ValueError:
        lo = 0
    try:
        hi = LADDER.index(ceiling)
    except ValueError:
        hi = len(LADDER) - 1
    if hi < lo:
        hi = lo  # a ceiling below the floor degenerates to a single tier
    return list(LADDER[lo : hi + 1])


class LLMGateway:
    """Stateless escalating wrapper over a :class:`ProviderRegistry`."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    # -- non-tool completion ------------------------------------------------- #

    async def complete(
        self,
        messages: list[Message],
        *,
        floor: str = "fast",
        ceiling: str = "powerful",
        purpose: str = "",
        **kwargs: Any,
    ) -> CompletionResult:
        """Escalating non-streaming completion.

        Tries ``floor`` first; on the ESCALATE sentinel steps up the ladder to
        ``ceiling``. Preserves the cost-tracker + resilience already injected on
        each provider (we call the same ``provider.complete``).
        """
        tiers = tier_span(floor, ceiling)
        result: CompletionResult | None = None
        for idx, tier in enumerate(tiers):
            can_escalate = idx < len(tiers) - 1
            provider, _degraded = self._registry.resolve_tier_with_fallback(tier)
            msgs = _augment_messages(messages, can_escalate)
            result = await provider.complete(msgs, model="", **kwargs)
            if can_escalate and is_escalate_signal(result.content):
                log.engine.info(
                    "[llm_gateway] complete: model escalated — stepping up tier",
                    extra={"_fields": {"purpose": purpose, "from_tier": tier,
                                       "to_tier": tiers[idx + 1]}},
                )
                continue
            return result
        # Unreachable in practice (tier_span never empty); satisfy the type.
        assert result is not None
        return result

    # -- agentic tool loop --------------------------------------------------- #

    async def complete_with_tools(
        self,
        *,
        user_text: str,
        system_text: str | None,
        tool_schemas: list[dict[str, Any]],
        tool_dispatcher: Any,
        floor: str = "fast",
        ceiling: str = "powerful",
        purpose: str = "",
        on_escalate: Any = None,
        **kwargs: Any,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Escalating agentic tool loop with FULL mid-loop escalation.

        Each attempt runs the whole ReAct loop on one tier. If the model returns
        the ESCALATE sentinel as its final answer (even after running tools), the
        draft is DISCARDED and the loop is re-run fresh on the next tier up. A
        tier whose provider can't run tools is skipped upward. ``on_escalate`` (an
        async callback) lets the caller reset turn-scoped state — e.g. the
        tool-outcome ledger — between attempts so a discarded attempt's tool
        failures don't poison the next tier's give-up floor.
        """
        tiers = tier_span(floor, ceiling)
        final_text, calls = "", []  # type: tuple[str, list[dict[str, Any]]]
        for idx, tier in enumerate(tiers):
            can_escalate = idx < len(tiers) - 1
            provider, _degraded = self._registry.resolve_tier_with_fallback(tier)
            if tool_schemas and not provider.supports_tools and can_escalate:
                # Can't run the loop on this tier — climb to a tool-capable one.
                log.engine.info(
                    "[llm_gateway] tools: tier not tool-capable — stepping up",
                    extra={"_fields": {"purpose": purpose, "skip_tier": tier}},
                )
                continue
            sys = _augment_system(system_text, can_escalate)
            final_text, calls = await provider.complete_with_tools(
                user_text=user_text, system_text=sys, tool_schemas=tool_schemas,
                tool_dispatcher=tool_dispatcher, **kwargs,
            )
            if can_escalate and is_escalate_signal(final_text):
                log.engine.info(
                    "[llm_gateway] tools: model escalated mid-loop — discard + step up",
                    extra={"_fields": {"purpose": purpose, "from_tier": tier,
                                       "to_tier": tiers[idx + 1], "tool_calls": len(calls)}},
                )
                if on_escalate is not None:
                    await on_escalate(tier, tiers[idx + 1])
                continue
            return final_text, calls
        return final_text, calls


def _augment_system(system_text: str | None, can_escalate: bool) -> str | None:
    """Append the escalation instruction to a system prompt (when escalation is
    still possible). Returns the input unchanged at the ceiling."""
    if not can_escalate:
        return system_text
    return (system_text or "") + ESCALATE_INSTRUCTION


def _augment_messages(messages: list[Message], can_escalate: bool) -> list[Message]:
    """Return a NEW message list with the escalation instruction folded into the
    system message (or a new leading system message). Input is never mutated."""
    if not can_escalate:
        return messages
    out: list[Message] = []
    injected = False
    for m in messages:
        if not injected and m.role == "system":
            out.append(Message(role="system", content=m.content + ESCALATE_INSTRUCTION))
            injected = True
        else:
            out.append(m)
    if not injected:
        out.insert(0, Message(role="system", content=ESCALATE_INSTRUCTION.strip()))
    return out
