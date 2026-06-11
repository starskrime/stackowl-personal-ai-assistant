"""Declarative adapter layer for capability-class substitution.

Maps between a failed tool's raw args and a canonical *normalized input* dict,
and from that normalized input back into any sibling tool's args.

This module is PURE (no I/O, no awaits).  The self-heal supervisor (T14) uses
these adapters to reroute a failed tool call without embedding tool-specific
logic in the dispatch loop.

Adding a new capability class is a one-step operation: add entries to
``_ADAPTERS``.  Each entry is an ``_ToolAdapter`` that owns two callables:
  - ``to_normalized``   – extract the canonical NormalizedInput from raw args
  - ``from_normalized`` – rebuild this tool's args from a NormalizedInput,
                          or return ``None`` when the tool cannot be served
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from stackowl.infra.observability import log

# ---------------------------------------------------------------------------
# NormalizedInput type alias (web_knowledge canonical form)
# ---------------------------------------------------------------------------
# For "web_knowledge" capability class the normalized representation is:
#   {"url": str, "query": str}
# An empty string means the field is unavailable.

NormalizedInput = dict[str, str]


# ---------------------------------------------------------------------------
# Adapter dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ToolAdapter:
    """Pair of callables: extract → NormalizedInput, build ← NormalizedInput."""

    to_normalized: Callable[[dict[str, object]], NormalizedInput]
    from_normalized: Callable[[NormalizedInput], dict[str, object] | None]


# ---------------------------------------------------------------------------
# Web-knowledge adapter helpers (pure functions, no I/O)
# ---------------------------------------------------------------------------

def _browse_to_normalized(args: dict[str, object]) -> NormalizedInput:
    url = str(args.get("seed_url") or "")
    query = str(args.get("task") or "")
    return {"url": url, "query": query}


def _web_search_to_normalized(args: dict[str, object]) -> NormalizedInput:
    query = str(args.get("query") or "")
    return {"url": "", "query": query}


def _web_fetch_to_normalized(args: dict[str, object]) -> NormalizedInput:
    url = str(args.get("url") or "")
    return {"url": url, "query": ""}


def _browse_from_normalized(ni: NormalizedInput) -> dict[str, object] | None:
    # browser_browse can work with either a url or a query.
    if not ni.get("url") and not ni.get("query"):
        return None
    result: dict[str, object] = {}
    if ni.get("url"):
        result["seed_url"] = ni["url"]
    if ni.get("query"):
        result["task"] = ni["query"]
    return result


def _web_search_from_normalized(ni: NormalizedInput) -> dict[str, object] | None:
    query = ni.get("query", "")
    if not query:
        return None
    return {"query": query}


def _web_fetch_from_normalized(ni: NormalizedInput) -> dict[str, object] | None:
    url = ni.get("url", "")
    if not url:
        return None
    return {"url": url}


# ---------------------------------------------------------------------------
# Declarative registry: tool_name → _ToolAdapter
# ---------------------------------------------------------------------------
# To add a new capability class, append entries here.  No other code changes.

_ADAPTERS: dict[str, _ToolAdapter] = {
    "browser_browse": _ToolAdapter(
        to_normalized=_browse_to_normalized,
        from_normalized=_browse_from_normalized,
    ),
    "web_search": _ToolAdapter(
        to_normalized=_web_search_to_normalized,
        from_normalized=_web_search_from_normalized,
    ),
    "web_fetch": _ToolAdapter(
        to_normalized=_web_fetch_to_normalized,
        from_normalized=_web_fetch_from_normalized,
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalized_input_for(
    failed_tool: str, failed_args: dict[str, object]
) -> NormalizedInput | None:
    """Extract the canonical NormalizedInput from a failed tool's raw args.

    Returns ``None`` when ``failed_tool`` has no registered adapter (unknown
    tool or capability class not yet covered).
    """
    log.engine.debug(
        "capability_substitution.normalized_input_for: entry",
        extra={"_fields": {"failed_tool": failed_tool}},
    )
    try:
        adapter = _ADAPTERS.get(failed_tool)
        if adapter is None:
            log.engine.debug(
                "capability_substitution.normalized_input_for: no adapter",
                extra={"_fields": {"failed_tool": failed_tool}},
            )
            return None
        result = adapter.to_normalized(failed_args)
        log.engine.debug(
            "capability_substitution.normalized_input_for: exit",
            extra={"_fields": {"failed_tool": failed_tool, "normalized_keys": list(result)}},
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.engine.error(
            "capability_substitution.normalized_input_for: failed",
            exc_info=exc,
            extra={"_fields": {"failed_tool": failed_tool}},
        )
        return None


def build_args_for(
    tool: str, normalized: NormalizedInput
) -> dict[str, object] | None:
    """Build ``tool``'s args from a NormalizedInput.

    Returns ``None`` when:
    - ``tool`` has no registered adapter, OR
    - the adapter determines the tool cannot be served (missing required fields).
    """
    log.engine.debug(
        "capability_substitution.build_args_for: entry",
        extra={"_fields": {"tool": tool}},
    )
    try:
        adapter = _ADAPTERS.get(tool)
        if adapter is None:
            log.engine.debug(
                "capability_substitution.build_args_for: no adapter",
                extra={"_fields": {"tool": tool}},
            )
            return None
        result = adapter.from_normalized(normalized)
        log.engine.debug(
            "capability_substitution.build_args_for: exit",
            extra={"_fields": {"tool": tool, "servable": result is not None}},
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.engine.error(
            "capability_substitution.build_args_for: failed",
            exc_info=exc,
            extra={"_fields": {"tool": tool}},
        )
        return None
