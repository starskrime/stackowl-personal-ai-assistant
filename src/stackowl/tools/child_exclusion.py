"""Child-exclusion rail — the canonical set of tools a delegated child must NOT run.

A delegated sub-agent runs at ``delegation_depth > 0``. Certain tools (spawn /
delegate / process / sandboxed code execution / owl-build) must be refused to such
a child so it cannot recurse into a fork-bomb or run arbitrary code (E8-S0 / E11-S5
GAP-B). The pipeline already filters these from a child's PRESENTED schema set and
re-checks at the dispatch seam; this module is the SHARED source of truth + a
``TraceContext``-based self-defense helper (SEC-3 / F163, F164) so a tool and the
batch executor can defend themselves WITHOUT depending on PipelineState (tools only
ever see :class:`~stackowl.infra.trace.TraceContext`).

Defense-in-depth, not the primary gate: the trusted pipeline state remains the
authoritative enforcement; these checks are belt-and-braces against a future call
path that reaches a tool's ``execute`` without the schema filter (e.g. a
pre-consented batch action).
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext

#: Tools a delegated child (delegation_depth>0) must NOT run. The canonical set —
#: the pipeline imports this so the schema filter, the dispatch-seam re-check, and
#: the per-tool self-defense never drift.
CHILD_EXCLUDED_TOOLS: frozenset[str] = frozenset(
    {
        "delegate_task", "sessions_spawn", "sessions_send", "process",
        "execute_code", "owl_build", "claude_code",
    }
)


def child_excluded_now(tool_name: str) -> bool:
    """Return True if ``tool_name`` is child-excluded AND we are at depth>0.

    Reads the acting turn's ``delegation_depth`` off :class:`TraceContext` (the only
    context a tool can see). Fail-closed on a malformed depth: a non-int / negative
    value is treated as ``>0`` (excluded) rather than slipping through.
    """
    if tool_name not in CHILD_EXCLUDED_TOOLS:
        return False
    depth_raw = TraceContext.get().get("delegation_depth", 0)
    try:
        depth = int(depth_raw)
    except (TypeError, ValueError):
        log.tool.warning(
            "[tools] child_exclusion: unparseable delegation_depth — failing closed",
            extra={"_fields": {"tool": tool_name, "depth_raw": repr(depth_raw)}},
        )
        return True
    return depth != 0
