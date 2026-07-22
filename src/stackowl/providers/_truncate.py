"""Bound tool-observation size so the tool loop can't overflow the model context.

Two independent, deterministic, LLM-free guards (model- and tool-agnostic):

* ``truncate_observation`` caps a SINGLE tool result at ``MAX_OBSERVATION_CHARS``
  chars, keeping the head and appending a clear truncation marker. Applied at
  every point a tool result enters the provider message list.
* ``trim_messages_to_budget`` enforces a TOTAL char budget across the whole
  message list by eliding the oldest tool-observation messages first, never the
  system message, the user's first request, or the two most recent messages.

Both are intentionally tiny, pure, and defensive — they never raise (a guard that
crashes the turn it is meant to protect is worse than the overflow it prevents).
"""

from __future__ import annotations

from typing import Any

from stackowl.infra.observability import log

# Raised 2026-07-22 (owner decision) — both are now pure backstops against a
# genuinely pathological single tool result / totally-unconfigured window, not
# shaping ceilings. Callers with a real resolved context_chars pass their own
# (unshrunk) budget to trim_messages_to_budget; CONTEXT_CHAR_BUDGET here is
# only the fallback when that's unavailable.
MAX_OBSERVATION_CHARS = 100_000  # ~25k tokens; a single tool result this large is pathological
CONTEXT_CHAR_BUDGET = 1_000_000  # ~250k tokens; fallback only, real callers pass context_chars

_ELIDED_PLACEHOLDER = "[earlier tool output elided to fit context]"


def truncate_observation(text: str | None, limit: int = MAX_OBSERVATION_CHARS) -> str:
    """Cap a tool result to ``limit`` chars, keeping the head, with a clear marker."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    marker = (
        f"\n…[output truncated: {omitted} chars omitted — use a more specific "
        "query or tool to get the part you need]"
    )
    # keep the head (most tools put the salient info first); reserve room for the marker
    return text[: max(0, limit - len(marker))] + marker


def _content_chars(content: Any) -> int:
    """Length of a message's content, handling str and anthropic list content."""
    if isinstance(content, str):
        return len(content)
    return len(str(content))


def _is_observation(message: dict[str, Any]) -> bool:
    """True if ``message`` is an elidable tool-observation turn (either protocol).

    * OpenAI native:  ``role == "tool"``
    * OpenAI ReAct:   ``role == "user"`` whose content str starts with "OBSERVATION:"
    * Anthropic:      ``role == "user"`` with a list content of tool_result dicts
    """
    role = message.get("role")
    content = message.get("content")
    if role == "tool":
        return True
    if role == "user":
        if isinstance(content, str) and content.startswith("OBSERVATION:"):
            return True
        if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            return True
    return False


def _elide(message: dict[str, Any]) -> None:
    """Replace a tool-observation message's content in place with a placeholder.

    For anthropic list content the tool_result blocks are kept (preserving their
    ``tool_use_id`` so tool_use/tool_result pairing stays valid), only their inner
    ``content`` is replaced with the placeholder.
    """
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                block["content"] = _ELIDED_PLACEHOLDER
    else:
        message["content"] = _ELIDED_PLACEHOLDER


def trim_messages_to_budget(
    messages: list[dict[str, Any]], budget: int = CONTEXT_CHAR_BUDGET
) -> list[dict[str, Any]]:
    """Elide oldest tool-observation messages until total content ≤ ``budget``.

    Protected (never elided): the system message (index 0 if role == "system"),
    the FIRST user message (the user's actual request), and the most recent 2
    messages. Anthropic tool_use/tool_result pairing is preserved because messages
    are never removed — only the CONTENT of tool_result blocks is replaced, so the
    message list shape (and pairing) is unchanged. Deterministic, no LLM call.

    Defensive: never raises. On any unexpected structure it logs at debug and
    returns ``messages`` unchanged.
    """
    try:
        if not messages:
            return messages
        total = sum(_content_chars(m.get("content", "")) for m in messages)
        if total <= budget:
            return messages

        n = len(messages)
        protected: set[int] = set()
        # System message at index 0.
        if messages[0].get("role") == "system":
            protected.add(0)
        # The first user message (the user's actual request).
        for i, m in enumerate(messages):
            if m.get("role") == "user":
                protected.add(i)
                break
        # The most recent two messages.
        protected.add(n - 1)
        if n >= 2:
            protected.add(n - 2)

        elided_count = 0
        for i, m in enumerate(messages):  # oldest -> newest
            if total <= budget:
                break
            if i in protected or not _is_observation(m):
                continue
            before = _content_chars(m.get("content", ""))
            _elide(m)
            after = _content_chars(m.get("content", ""))
            total -= before - after
            elided_count += 1

        if elided_count:
            log.engine.debug(
                "[truncate] trim_messages_to_budget: elided old observations",
                extra={
                    "_fields": {
                        "elided": elided_count,
                        "budget": budget,
                        "final_chars": total,
                    }
                },
            )
        return messages
    except Exception as exc:  # never raise — a crashing guard is worse than overflow
        log.engine.debug(
            "[truncate] trim_messages_to_budget: unexpected structure — returning unchanged",
            extra={"_fields": {"error": str(exc)}},
        )
        return messages
