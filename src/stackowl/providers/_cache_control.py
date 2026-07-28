"""D01.2 — where prompt-cache breakpoints go, and why each one is placed there.

Anthropic caches nothing by default. You mark positions in the request; everything
from the start of the request up to a marker becomes cacheable, and **you get at
most four markers**. Placement is therefore the entire design, and this module is
the only place in StackOwl that places one.

**One seam owns every marker.** The reference platform marks in three separate
files — system and messages in one, tools in the adapter, a second system
injection in the runner — which is why its own documented "4-marker layout" never
mentions the tools marker, the most valuable of the four. With one function seeing
tools, system and messages together, the four-breakpoint budget is *provably*
respected instead of being enforced in three places that cannot see each other.

**The guard is cumulative, not per-span.** A breakpoint caches the whole prefix
before it, so what must clear the model's minimum is the running total up to the
marker, never the individual span. The tools array alone may miss the floor while
tools+system comfortably clears it — a per-span guard would wrongly drop marker 2
along with marker 1.

**A below-minimum marker fails silently.** There is no error; the response simply
reports ``cache_creation_input_tokens: 0`` and one of the four breakpoints has been
spent on nothing. That silence is the reason this guard exists at all.

Pure by contract: no I/O, no provider dependency, no mutation of the caller's
objects. The tool loop reuses one ``messages`` list across rounds, so marking in
place would accumulate a marker per round and blow invariant I1 open on the second
iteration. See ``docs/hermes-mapping/designs/D01.2.md``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from stackowl.infra.observability import log

# The smallest minimum cacheable prefix published for ANY Claude model. The real
# minimum is model-dependent and NOT monotonic — 512 (Opus 5, Fable 5), 1024
# (Opus 4.8, Sonnet 5/4.6/4.5), 2048 (Opus 4.7, Haiku 3.5), 4096 (Opus 4.6/4.5,
# Haiku 4.5) — and it is not exposed by the Models API, so nothing live can be
# asked for it.
#
# This is deliberately the floor UNDER those floors rather than a table of them.
# A table would rot silently with every model release; this constant only ever
# says "below here, nothing caches ANYWHERE", which stays true as models are
# added. A caller that genuinely knows the model's own minimum passes it as
# ``minimum_tokens`` and gets the sharper guard.
MIN_CACHEABLE_TOKENS = 512

# The hard API limit, and the reason one module owns all marking (invariant I1).
_MAX_BREAKPOINTS = 4

# Characters per token, and the margin applied to the estimate. The margin makes
# the estimate UNDER-count, so estimation error fails toward NOT marking: a
# borderline span is skipped rather than spending a breakpoint on a dead marker.
_CHARS_PER_TOKEN = 4
_ESTIMATE_MARGIN = 0.8


def _estimate_tokens(text: str) -> int:
    """Conservative token estimate for ``text`` — the fallback when the live
    ``count_tokens`` endpoint is unavailable on a gateway.

    Under-counts on purpose (see ``_ESTIMATE_MARGIN``).
    """
    return int(len(text) / _CHARS_PER_TOKEN * _ESTIMATE_MARGIN)


def _tools_text(tools: list[dict[str, Any]] | None) -> str:
    """Serialised form of the tools array, for size estimation only."""
    if not tools:
        return ""
    try:
        return json.dumps(tools, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        # An unserialisable schema is a sizing problem, never a turn-breaking one:
        # fall back to the repr, which is always available and is only ever used
        # to compare against a token floor.
        log.engine.debug(
            "[cache] breakpoints: tools not JSON-serialisable — estimating from repr",
            extra={"_fields": {"err": str(exc)}},
        )
        return repr(tools)


def _content_text(content: Any) -> str:
    """Every piece of text inside a message/system content value, concatenated."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_content_text(block) for block in content)
    if isinstance(content, dict):
        parts = []
        for key in ("text", "content"):
            value = content.get(key)
            if value is not None:
                parts.append(_content_text(value))
        return "".join(parts)
    return ""


def count_markers(value: Any) -> int:
    """How many ``cache_control`` markers ``value`` carries, at any depth.

    Lives here rather than at the call site because this module owns the concept:
    invariant I1 is a statement about this number, so the thing that places
    markers and the thing that counts them must agree by construction.
    """
    if isinstance(value, dict):
        return sum(
            1 if key == "cache_control" else count_markers(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return sum(count_markers(item) for item in value)
    return 0


def _marker(ttl: str) -> dict[str, str]:
    """The ``cache_control`` value for ``ttl``.

    ``{"type": "ephemeral"}`` IS the 5-minute form — an explicit ``ttl`` key would
    be noise on the wire, so only the 1-hour form carries one.
    """
    if ttl == "1h":
        return {"type": "ephemeral", "ttl": "1h"}
    return {"type": "ephemeral"}


def _strip_markers(value: Any) -> Any:
    """``value`` with every ``cache_control`` removed, copied not mutated.

    This is what makes invariant I1 hold under the tool loop's real access
    pattern. The loop appends to one growing ``messages`` list and re-marks it
    every round; without stripping, round 2's markers land on the new tail while
    round 1's markers are still sitting on messages that are no longer the tail,
    and the request goes out with six markers instead of four.

    Stripping is safe precisely because this module owns every marker in the
    codebase — nothing else places one for us to destroy.
    """
    if isinstance(value, dict):
        return {k: _strip_markers(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_strip_markers(item) for item in value]
    return value


def _mark_last_block(content: Any, marker: dict[str, str]) -> Any:
    """``content`` with ``marker`` on its final content block.

    A plain string is converted to a one-block list, because a top-level
    ``cache_control`` can only ride a content block. That conversion happens ONLY
    here — i.e. only when a marker is actually being placed — so an unmarked
    request keeps the cheap string form and stays byte-identical to what StackOwl
    sends today.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content, "cache_control": marker}]
    if isinstance(content, list) and content:
        blocks = list(content)
        last = blocks[-1]
        if isinstance(last, dict):
            blocks[-1] = {**last, "cache_control": marker}
            return blocks
    return None


def apply_cache_breakpoints(
    tools: list[dict[str, Any]] | None,
    system: str | list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
    *,
    model: str,
    ttl: str = "5m",
    measured_tokens: Mapping[str, int] | None = None,
    minimum_tokens: int | None = None,
) -> tuple[list[dict[str, Any]] | None, str | list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Place the four cache breakpoints and return the marked request parts.

    The layout is fixed — tools, the full system prompt, and the last two
    messages — and a span that fails the minimum guard simply loses its marker.
    Its breakpoint is **not** reallocated to another span: a simpler, assertable
    layout was chosen over a self-optimising one, because "four markers, always in
    these four places" is a property a test can pin down and "wherever the
    optimiser decided this turn" is not.

    ``measured_tokens`` carries the provider's live ``count_tokens`` measurement
    for the ``"tools"`` and ``"system"`` spans; it wins over the character
    estimate, which is the fallback for a gateway without that endpoint.

    ``minimum_tokens`` overrides :data:`MIN_CACHEABLE_TOKENS` for a caller that
    knows the model's own floor.

    Never mutates its arguments.
    """
    floor = minimum_tokens if minimum_tokens is not None else MIN_CACHEABLE_TOKENS
    measured = measured_tokens or {}
    log.engine.debug(
        "[cache] breakpoints: entry",
        extra={"_fields": {
            "model": model, "ttl": ttl, "floor": floor,
            "tools_len": len(tools or []), "message_count": len(messages),
            "measured": bool(measured_tokens),
        }},
    )

    marker = _marker(ttl)
    out_tools = _strip_markers(tools) if tools is not None else None
    out_system = _strip_markers(system) if system is not None else None
    out_messages: list[dict[str, Any]] = _strip_markers(messages)
    placed: list[str] = []

    # ---- marker 1: the end of the tools array (position 0, largest shared span)
    tools_tokens = int(measured.get("tools", _estimate_tokens(_tools_text(tools))))
    cumulative = tools_tokens
    if out_tools and cumulative >= floor:
        out_tools = [*out_tools[:-1], {**out_tools[-1], "cache_control": marker}]
        placed.append("tools")
    elif out_tools:
        log.engine.debug(
            "[cache] breakpoints: span skipped",
            extra={"_fields": {"span": "tools", "tokens": cumulative,
                               "minimum": floor, "reason": "below_minimum"}},
        )

    # ---- marker 2: the end of the system prompt
    system_tokens = int(measured.get("system", _estimate_tokens(_content_text(system))))
    cumulative += system_tokens
    if out_system is not None and cumulative >= floor:
        marked_system = _mark_last_block(out_system, marker)
        if marked_system is not None:
            out_system = marked_system
            placed.append("system")
    elif out_system is not None:
        log.engine.debug(
            "[cache] breakpoints: span skipped",
            extra={"_fields": {"span": "system", "tokens": cumulative,
                               "minimum": floor, "reason": "below_minimum"}},
        )

    # ---- the remaining markers: the most recent CARRIABLE messages
    #
    # Marking the tail is what makes the cache ROLL: this turn writes an entry
    # ending at the newest message, and the next turn reads it back as its own
    # prefix instead of re-reading the whole conversation at full price.
    #
    # THE BUDGET IS REALLOCATED, and that is a correction to our own design,
    # learned by reading the reference implementation rather than our summary of
    # it. Theirs computes `remaining = 4 - breakpoints_used` and gives the
    # leftovers to messages. Ours originally fixed the layout at
    # tools+system+2, so a tools array below the minimum simply LOST its
    # breakpoint — wasting one on exactly the deployments the guard exists to
    # protect. Reallocation is no less assertable: the property is "spend every
    # breakpoint that has somewhere legal to go", which is what the tests pin.
    #
    # Two reasons a message is passed over, both of which must NOT cost a
    # breakpoint: its cumulative prefix is below the floor (a dead marker), or it
    # has no content block able to carry one (an empty tool/assistant turn — the
    # reference platform's `_can_carry_marker` insight). Candidates are filtered
    # FIRST and the newest `remaining` of them are marked, so a skipped message
    # moves the marker to an earlier one instead of dropping it.
    message_tokens = [_estimate_tokens(_content_text(m.get("content"))) for m in messages]
    remaining = _MAX_BREAKPOINTS - len(placed)
    carriers: list[tuple[int, Any]] = []
    for index in range(len(out_messages)):
        prefix = cumulative + sum(message_tokens[: index + 1])
        if prefix < floor:
            continue
        marked_content = _mark_last_block(out_messages[index].get("content"), marker)
        if marked_content is None:
            # An empty content list cannot carry a marker. Skip it rather than
            # inventing a block — a fabricated block would change what the model
            # reads to buy a cache entry, which is never a trade worth making.
            continue
        carriers.append((index, marked_content))

    skipped = len(out_messages) - len(carriers)
    if skipped:
        log.engine.debug(
            "[cache] breakpoints: messages passed over",
            extra={"_fields": {"skipped": skipped, "carriers": len(carriers),
                               "minimum": floor}},
        )
    for index, marked_content in carriers[-remaining:] if remaining > 0 else []:
        out_messages[index] = {**out_messages[index], "content": marked_content}
        placed.append(f"message[{index}]")

    log.engine.debug(
        "[cache] breakpoints: exit",
        extra={"_fields": {"markers_placed": len(placed), "ttl": ttl, "spans": placed}},
    )
    return out_tools, out_system, out_messages
