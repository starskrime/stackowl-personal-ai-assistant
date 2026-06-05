"""bounds_guard — enforce the TOOLS bounds axis at the dispatch seam (E2-S1, FR33).

A single pure helper, :func:`check_tool_bounds`, decides whether the acting owl's
bounds permit a dispatched tool. It returns a clean, user-facing block-reason
string when the tool is refused, or ``None`` when dispatch may proceed. It never
raises — a tool outside bounds is *reported cleanly* (FR33: "stays within them and
reports cleanly when blocked"), not crashed.

Relationship to consent: bounds are a HARD capability allowlist (the owl cannot
use the tool at all). Consent is human approval for a consequential tool. Bounds
are checked before consent/execution; a tool outside bounds is refused regardless
of consent.

FR35 DELEGATION GAP (tracked, NOT fixed here — lands in Epic 3): a DELEGATED
sub-owl currently runs under ITS OWN bounds, not the parent's. There is no
parent∩child bounds intersection at the delegation seam yet, so a sub-owl could
in principle hold a tool the parent lacks. The narrowing-only composition
primitive for that fix already exists (:meth:`BoundsSpec.intersect`); wiring it
into the delegation dispatch (no-escalation-via-delegation) is the FR35 follow-up
in Epic 3. Documented here so the gap is explicit, never silent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.owls.manifest import OwlAgentManifest


def check_tool_bounds(
    owl_manifest: OwlAgentManifest | None,
    tool_name: str,
) -> str | None:
    """Return a block-reason if the owl's bounds forbid ``tool_name``, else None.

    No bounds, or a ``tools`` axis of ``None`` (unrestricted), returns ``None``
    so an unbounded owl is byte-for-byte unchanged.
    """
    # 1. ENTRY
    log.engine.debug(
        "[authz] bounds_guard.check: entry",
        extra={"_fields": {
            "tool": tool_name,
            "owl": getattr(owl_manifest, "name", None),
            "has_bounds": owl_manifest is not None and owl_manifest.bounds is not None,
        }},
    )

    # 2. DECISION — no manifest or no bounds → unbounded (legacy behavior).
    if owl_manifest is None or owl_manifest.bounds is None:
        log.engine.debug(
            "[authz] bounds_guard.check: no bounds — unrestricted",
            extra={"_fields": {"tool": tool_name}},
        )
        return None

    bounds = owl_manifest.bounds

    # 3. STEP — consult the tools axis. None tools axis = unrestricted.
    if bounds.permits_tool(tool_name):
        log.engine.debug(
            "[authz] bounds_guard.check: tool permitted by bounds",
            extra={"_fields": {"tool": tool_name, "owl": owl_manifest.name}},
        )
        return None

    # 4. EXIT — BLOCKED. A clean report is returned. The single authoritative
    # block log (WARNING, with trace_id) is emitted by the dispatch caller in
    # execute.py — this DEBUG keeps the pure helper non-silent without duplicating
    # that line (the consolidated double-log fix).
    log.engine.debug(
        "[authz] bounds_guard.check: tool outside owl bounds — blocking",
        extra={"_fields": {
            "tool": tool_name,
            "owl": owl_manifest.name,
            "axis": "tools",
        }},
    )
    return (
        f"The action '{tool_name}' is not permitted by this owl's bounds and was "
        "not run. This owl is restricted to a fixed set of tools; choose one of its "
        "permitted tools or answer the user directly."
    )
