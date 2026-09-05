"""Salvage — turn a step-capped turn's tool results into an answer (Stage 1a).

MEASURED, trace ``f33c9fa0``: an 18-character question ("What agents i have") ran 16
rounds, billed 683,728 input tokens, hit the 20-step cap and delivered only
``"[stopped: I ran out of steps ...]"``. The findings were never lost — execute.py
holds every tool result at the breach and hands ``synthesize_floor`` an empty
``attempts`` list. This module makes that last inch honest.

WHY NOT ``synthesize_floor(attempts=...)`` ALONE. ``attempts`` renders as a
comma-joined list of tool NAMES. "I tried owl_list, skills_list" is more honest than
silence and still does not answer the question; the answer lives in ``result``.

WHY BUILD FROM RESULTS RATHER THAN RE-SENDING THE TRANSCRIPT. The reference platform
re-sends the whole conversation with tools stripped. Building from the tool records
is strictly cheaper and better suited here:

  * **toolless** — none of the 19,423 tokens/round of schemas ride along;
  * **no reasoning text**, only results (Bakir, 2026-08-29: *"reasoning text should
    not be in memory, only the result of the reason"*);
  * **no wire-format conversion**, so it cannot drift between the anthropic and
    openai message shapes.

THE COST. Exactly one bounded extra call, capped at ``_TOTAL_BUDGET_CHARS``. On the
measured trace that is ~6k tokens against 683,728 already spent, and it is the
difference between delivering nothing and delivering the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from stackowl.infra.observability import log
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.pipeline.state import ToolCall

# One result may occupy at most this much of the salvage prompt. A browser dump is
# megabytes; the head of a result carries the identifying content (title, first rows,
# error line) and the tail is almost always boilerplate.
_PER_RESULT_CHARS = 2_000

# Hard ceiling on the whole prompt. The salvage call must never become the runaway it
# exists to end — this bounds it at roughly 6k tokens regardless of what the turn did.
_TOTAL_BUDGET_CHARS = 24_000


class _Completer(Protocol):
    async def complete(
        self, messages: list[Message], model: str, **kwargs: object,
    ) -> Any: ...


def _clip(text: str, limit: int) -> tuple[str, bool]:
    """Return ``(text, was_clipped)`` — clipping is always announced by the caller."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def build_salvage_messages(
    goal: str | None,
    tool_calls: list[ToolCall] | tuple[ToolCall, ...],
    *,
    per_result_chars: int = _PER_RESULT_CHARS,
    total_budget_chars: int = _TOTAL_BUDGET_CHARS,
) -> list[Message]:
    """Build the toolless salvage request. Pure — no I/O, never raises.

    Returns ``[]`` when there is nothing worth summarising, which the caller MUST
    treat as "make no call": a model round that can only say "I found nothing" costs
    tokens to restate the silence we are already able to produce for free.

    Selection is newest-first (the most recent results are the ones the turn was
    converging on) under ``total_budget_chars``, then rendered in the original order
    so the narrative still reads forwards. Omissions are always announced.
    """
    records = list(tool_calls or ())

    # A failed call is not a finding. Its text is an error message, and trace
    # 0e568f1a already paid for delivering one of those as though it were the answer.
    findings = [
        tc for tc in records
        if tc.error is None and (tc.result or "").strip()
    ]

    # Dedup byte-identical results. A stuck loop repeats one call; paying for the
    # same page twelve times buys nothing. First occurrence wins (keeps the order).
    seen: set[str] = set()
    unique: list[ToolCall] = []
    for tc in findings:
        body = (tc.result or "").strip()
        if body in seen:
            continue
        seen.add(body)
        unique.append(tc)

    if not unique:
        log.engine.debug(
            "[budget] salvage: nothing to summarise",
            extra={"_fields": {"records": len(records), "findings": len(findings)}},
        )
        return []

    # Greedy newest-first under the total budget.
    chosen_idx: set[int] = set()
    used = 0
    clipped_any = False
    rendered: dict[int, str] = {}
    for idx in range(len(unique) - 1, -1, -1):
        tc = unique[idx]
        body, was_clipped = _clip((tc.result or "").strip(), per_result_chars)
        entry = f"[{tc.tool_name}] {body}" + (" …[truncated]" if was_clipped else "")
        if used + len(entry) > total_budget_chars and chosen_idx:
            break
        used += len(entry)
        clipped_any = clipped_any or was_clipped
        chosen_idx.add(idx)
        rendered[idx] = entry

    omitted = len(unique) - len(chosen_idx)
    n_failed = len(records) - len(findings)

    lines = [rendered[i] for i in sorted(chosen_idx)]
    if omitted:
        lines.append(f"…[{omitted} earlier result(s) omitted to stay in budget]")
    if n_failed:
        lines.append(f"…[{n_failed} tool call(s) failed and produced no finding]")

    findings_block = "\n\n".join(lines)

    system = (
        # CAP-NEUTRAL, deliberately. This said "You ran out of steps", which is
        # false on a token breach — four of five breaches on 2026-09-05. The model
        # does not need to know WHICH budget ended the turn to answer from
        # evidence, so the honest sentence is the one that asserts nothing extra.
        "You stopped before finishing. Below are the tool results you "
        "already obtained — evidence only, no reasoning. Answer the user's question "
        "directly from that evidence.\n"
        "Be concrete and brief. Do not describe your process, do not apologise, and "
        "do not speculate. If the evidence does not answer the question, say in one "
        "line exactly what is still missing."
    )
    user = (
        f"QUESTION: {goal or '(not recorded)'}\n\n"
        f"WHAT YOU FOUND:\n{findings_block}"
    )

    log.engine.info(
        "[budget] salvage: request built",
        extra={"_fields": {
            "records": len(records), "findings": len(findings),
            "unique": len(unique), "presented": len(chosen_idx),
            "omitted": omitted, "failed": n_failed,
            "clipped": clipped_any, "prompt_chars": len(system) + len(user),
        }},
    )
    return [Message(role="system", content=system), Message(role="user", content=user)]


async def summarize_findings(
    provider: _Completer,
    model: str,
    goal: str | None,
    tool_calls: list[ToolCall] | tuple[ToolCall, ...],
) -> str | None:
    """One toolless call that turns the turn's findings into an answer.

    Returns the summary, or ``None`` when there was nothing to summarise, the
    provider failed, or the model returned nothing. **Never raises** — this runs on
    the way out of an already-capped turn, and an exception here would replace a
    partial answer with a crash, which is strictly worse than the silence it exists
    to fix. On ``None`` the caller falls back to the existing honest floor.
    """
    messages = build_salvage_messages(goal, tool_calls)
    if not messages:
        return None

    try:
        # disable_thinking: a capped salvage budget spent on invisible reasoning
        # returns empty and degrades to the floor, which reads as a normal salvage.
        result = await provider.complete(messages, model, disable_thinking=True)
    except Exception as exc:  # noqa: BLE001 — degrade to the floor, never crash the exit
        log.engine.warning(
            "[budget] salvage: provider failed — falling back to the floor",
            extra={"_fields": {"error": str(exc), "model": model}},
        )
        return None

    content = str(getattr(result, "content", "") or "").strip()
    if not content:
        log.engine.warning(
            "[budget] salvage: model returned nothing — falling back to the floor",
            extra={"_fields": {"model": model}},
        )
        return None

    log.engine.info(
        "[budget] salvage: delivered findings instead of an empty stop",
        extra={"_fields": {"model": model, "summary_chars": len(content),
                           "n_tool_calls": len(list(tool_calls or ()))}},
    )
    return content
