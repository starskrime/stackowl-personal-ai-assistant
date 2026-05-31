"""Real-time persistence enforcer — the deliver-vs-giveup judge (Phase D).

The model carries the agentic charter AND the full tool catalog (incl. shell),
yet on hard tasks it can dispatch 1-3 read-only tools then GIVE UP with a polished
refusal, never escalating (shell / install / authoring a skill / searching for a
method). The charter is advisory; nothing in the live path catches a give-up.

This module is that catch. Before a turn's final answer is accepted, an LLM judge
decides — in the user's OWN language-agnostic intent, with NO hardcoded phrase
matching — whether the agent actually DELIVERED the requested outcome or gave up /
refused / deferred without exhausting its capabilities. On give-up the tool loop
injects :data:`PERSISTENCE_DIRECTIVE` and CONTINUES, so the agent escalates.

Modelled on :class:`stackowl.memory.critic_prompt.CriticScorerPromptBuilder` /
``parse_critic_response`` — same prompt shape, same strict-JSON parse via the shared
:func:`stackowl.memory.json_parser.parse_json_response`.

GLOBAL constraints honoured here:
  * Language-agnostic: the judge is an LLM that reads INTENT; no English (or any
    language) refusal phrases anywhere. No domain/case words.
  * Fail-OPEN: any parse/LLM error returns ``(True, "judge-error")`` — never hang,
    crash, or loop the turn; a broken judge must not block the user's answer.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.memory.json_parser import parse_json_response
from stackowl.providers.base import Message, ModelProvider

__all__ = ["PERSISTENCE_DIRECTIVE", "judge_delivery"]

# GLOBAL corrective directive — injected when the judge rules the agent gave up.
# Deliberately capability-oriented and case-free: no domain words, no tool brand
# names, no language-specific refusal vocabulary. It tells the agent what it still
# CAN do and forbids the give-up shapes (refuse / apologise / defer).
PERSISTENCE_DIRECTIVE = (
    "You have not yet delivered the requested outcome, and you have not exhausted "
    "your capabilities (you can run commands, install or build what you need, "
    "author a skill, or search for a method). Do not refuse, apologize, or defer. "
    "Continue now and complete the task."
)

# Truncation budgets — keep the judge prompt compact (mirrors critic's 2000-char caps).
_REQUEST_CAP = 2000
_DRAFT_CAP = 2000
_TOOLS_CAP = 40


def _build_messages(
    user_request: str, draft_answer: str, tools_tried: list[str]
) -> list[Message]:
    """Build the compact, GLOBAL judge prompt (no domain/language-specific content)."""
    tool_list = ", ".join(tools_tried[:_TOOLS_CAP]) if tools_tried else "(none)"
    system = Message(
        role="system",
        content=(
            "You are a delivery judge for an autonomous AI agent that can run "
            "commands, install or build software, author new skills, and search "
            "for methods. Given the user's request, the agent's draft reply, and "
            "the tools the agent used this turn, decide — by the user's intent in "
            "ANY language — whether the agent DELIVERED or gave up.\n\n"
            "DELIVERED (delivered=true) — any of these:\n"
            "  • The agent produced the requested outcome.\n"
            "  • The agent asked ONE necessary clarifying question because the "
            "request is genuinely ambiguous and cannot proceed without that "
            "information — asking is taking action, not giving up.\n"
            "  • The agent stated a specific, concrete, factual blocker (e.g. a "
            "required credential it cannot obtain, a hardware resource it does not "
            "have) after making real attempts.\n\n"
            "GAVE UP (delivered=false) — the agent refused, apologized, or "
            "deferred WITHOUT exhausting its capabilities: it could have run a "
            "command, installed or built something, authored a skill, or searched "
            "for a method — but did not. An agent that only explains why it cannot "
            "proceed or delegates the work back to the user without trying has NOT "
            "delivered.\n\n"
            "Return ONLY a JSON object — no prose, no markdown fences. Schema: "
            '{"delivered": true|false, "reason": "<one short sentence>"}.'
        ),
    )
    user = Message(
        role="user",
        content=(
            f"USER REQUEST:\n{user_request[:_REQUEST_CAP]}\n\n"
            f"AGENT DRAFT REPLY:\n{draft_answer[:_DRAFT_CAP]}\n\n"
            f"TOOLS USED THIS TURN: {tool_list}\n\n"
            "Decide whether the agent delivered the requested outcome or gave up "
            "without exhausting its capabilities.\n"
            'Output exactly: {"delivered": true, "reason": "..."}'
        ),
    )
    return [system, user]


async def judge_delivery(
    provider: ModelProvider,
    user_request: str,
    draft_answer: str,
    tools_tried: list[str],
) -> tuple[bool, str]:
    """Judge whether ``draft_answer`` delivered ``user_request``.

    Returns ``(delivered, reason)``. ``delivered`` is False ONLY when the judge
    explicitly rules a give-up. On ANY failure (provider error, unparseable
    output, missing/badly-typed key) this fails OPEN — returns ``(True,
    "judge-error")`` — so a broken judge never blocks the user's answer.
    """
    # 1. ENTRY
    log.engine.debug(
        "[persistence] judge_delivery: entry",
        extra={"_fields": {
            "request_len": len(user_request),
            "draft_len": len(draft_answer),
            "tools_tried": tools_tried[:_TOOLS_CAP],
        }},
    )
    messages = _build_messages(user_request, draft_answer, tools_tried)

    # 3. STEP — provider call (fail open on any provider error)
    try:
        result = await provider.complete(messages, model="")
    except Exception as exc:  # fail OPEN — never block the turn on a judge error
        log.engine.error(
            "[persistence] judge_delivery: provider.complete failed — failing open",
            exc_info=exc,
        )
        return True, "judge-error"

    # 2. DECISION — parse strict JSON (fail open on unparseable / wrong type)
    obj = parse_json_response(result.content, required_keys=["delivered"])
    if obj is None:
        log.engine.error(
            "[persistence] judge_delivery: unparseable judge output — failing open",
            extra={"_fields": {"raw_preview": result.content[:200]}},
        )
        return True, "judge-error"
    delivered = obj.get("delivered")
    if not isinstance(delivered, bool):
        log.engine.error(
            "[persistence] judge_delivery: 'delivered' not a bool — failing open",
            extra={"_fields": {"got": type(delivered).__name__}},
        )
        return True, "judge-error"
    reason = obj.get("reason")
    reason_str = reason if isinstance(reason, str) else ""

    # 4. EXIT
    log.engine.debug(
        "[persistence] judge_delivery: exit",
        extra={"_fields": {"delivered": delivered, "reason": reason_str[:120]}},
    )
    return delivered, reason_str
