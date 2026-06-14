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
from typing import Any, Protocol

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


# ---------------------------------------------------------------------------
# Substitute selection (W3.T14) — the recovery actuator's choice function.
# ---------------------------------------------------------------------------
# A sibling is eligible for AUTO-substitution only when it is NON-consequential
# (read/write). A consequential sibling is NEVER auto-run: it would bypass the
# consent gate. So the consent boundary is preserved BY CONSTRUCTION here.

# Severity ranking — read before write (deterministic priority: prefer the
# least-privileged sibling that can serve the same capability). Consequential is
# absent from this map → excluded from substitution entirely.
_SUBSTITUTABLE_SEVERITY_RANK: dict[str, int] = {"read": 0, "write": 1}


class _RegistryLike(Protocol):
    """Minimal registry surface ``find_substitute`` needs (kept narrow so the
    function stays testable without importing the real ToolRegistry).

    Returns are ``Any`` so any registry whose tools expose ``.name`` /
    ``.manifest`` satisfies the protocol (list invariance would otherwise reject
    the concrete ``ToolRegistry.all() -> list[Tool]``)."""

    def get(self, name: str) -> Any: ...

    def all(self) -> list[Any]: ...


def find_substitute(
    failed_tool: str,
    failed_args: dict[str, object],
    *,
    registry: _RegistryLike,
    in_bounds: Callable[[str], bool],
    already_substituted: set[str],
) -> tuple[str, dict[str, object]] | None:
    """Pick a NON-consequential, in-bounds sibling that can serve the same
    capability as a just-failed tool, or ``None``.

    Selection rules (ALL must hold for a candidate sibling ``s``):
      (a) ``s`` shares ``failed_tool``'s ``capability_tag`` (and the tag is set),
      (b) ``s.manifest.action_severity`` ∈ {"read", "write"} — NOT consequential
          (CONSENT-SAFETY: a consequential sibling is never auto-run),
      (c) the capability tag is not already in ``already_substituted`` (one
          substitution per capability per turn),
      (d) ``in_bounds(s.name)`` is True (bounds-safety),
      (e) ``build_args_for(s.name, normalized_input_for(failed_tool, failed_args))``
          returns non-None (the sibling can actually be served).

    Deterministic priority: read before write, then registry enumeration order.
    Returns ``(sibling_name, built_args)`` for the highest-priority match, else
    ``None``. NEVER raises — on any internal error it logs and returns ``None``
    (honest degradation: the caller falls through to the original failure).
    """
    log.engine.debug(
        "capability_substitution.find_substitute: entry",
        extra={"_fields": {
            "failed_tool": failed_tool,
            "already_substituted": sorted(already_substituted),
        }},
    )
    try:
        failed = registry.get(failed_tool)
        tag = getattr(getattr(failed, "manifest", None), "capability_tag", None)
        # (a) the failed tool must declare a capability tag; (c) and not already used.
        if not tag:
            log.engine.debug(
                "capability_substitution.find_substitute: failed tool has no tag",
                extra={"_fields": {"failed_tool": failed_tool}},
            )
            return None
        if tag in already_substituted:
            log.engine.debug(
                "capability_substitution.find_substitute: tag already substituted this turn",
                extra={"_fields": {"failed_tool": failed_tool, "tag": tag}},
            )
            return None

        normalized = normalized_input_for(failed_tool, failed_args)
        if normalized is None:
            log.engine.debug(
                "capability_substitution.find_substitute: no normalized input",
                extra={"_fields": {"failed_tool": failed_tool}},
            )
            return None

        # Build a capability_tag -> [(idx, tool)] index in one pass and only walk
        # the failed tag's bucket, instead of severity/bounds/build-checking every
        # registered tool on each failed-tool recovery attempt (F097). The original
        # enumerate() index is preserved as the deterministic tiebreak key.
        tag_index: dict[str, list[tuple[int, Any]]] = {}
        for idx, tool in enumerate(registry.all()):
            manifest = getattr(tool, "manifest", None)
            if manifest is None:
                continue
            t_tag = getattr(manifest, "capability_tag", None)
            if not t_tag:
                continue
            tag_index.setdefault(str(t_tag), []).append((idx, tool))

        candidates: list[tuple[int, int, str, dict[str, object]]] = []
        for idx, tool in tag_index.get(tag, []):
            manifest = getattr(tool, "manifest", None)
            name = getattr(tool, "name", None)
            if manifest is None or not isinstance(name, str):
                continue
            if name == failed_tool:
                continue
            # (b) CONSENT-SAFETY — only read/write may be auto-substituted.
            severity = getattr(manifest, "action_severity", None)
            rank = _SUBSTITUTABLE_SEVERITY_RANK.get(str(severity))
            if rank is None:
                continue  # consequential (or unknown) → never auto-run
            # (d) bounds-safety.
            try:
                if not in_bounds(name):
                    continue
            except Exception as exc:  # noqa: BLE001 — a bounds-probe fault excludes (safe)
                log.engine.warning(
                    "capability_substitution.find_substitute: in_bounds raised — skipping",
                    exc_info=exc,
                    extra={"_fields": {"sibling": name}},
                )
                continue
            # (e) servable — the sibling can be built from the normalized input.
            built = build_args_for(name, normalized)
            if built is None:
                continue
            candidates.append((rank, idx, name, built))

        if not candidates:
            log.engine.debug(
                "capability_substitution.find_substitute: no eligible sibling",
                extra={"_fields": {"failed_tool": failed_tool, "tag": tag}},
            )
            return None

        # Deterministic: lowest severity rank first, then registry order.
        candidates.sort(key=lambda c: (c[0], c[1]))
        _rank, _idx, chosen_name, chosen_args = candidates[0]
        log.engine.info(
            "capability_substitution.find_substitute: exit — sibling chosen",
            extra={"_fields": {
                "failed_tool": failed_tool, "tag": tag, "sibling": chosen_name,
            }},
        )
        return chosen_name, chosen_args
    except Exception as exc:  # noqa: BLE001 — the actuator must never crash a turn
        log.engine.error(
            "capability_substitution.find_substitute: failed",
            exc_info=exc,
            extra={"_fields": {"failed_tool": failed_tool}},
        )
        return None
