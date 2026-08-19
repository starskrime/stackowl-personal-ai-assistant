"""bounds_guard — pure bounds narrowing and effective-bounds checking (E2-S1/S2, FR33).

This module provides two core primitives:
- :func:`effective_bounds` — fold N optional bounds specs into one via intersection
  (narrowing-only composition of owl bounds ∩ creation_ceiling; enforcement only)
- :func:`check_effective_bounds` — return a block-reason if effective bounds forbid
  a tool, or None when dispatch may proceed. Never raises — a tool outside bounds
  is *reported cleanly* (FR33: "stays within them and reports cleanly when blocked"),
  not crashed. task_envelope is excluded from enforcement (E2-S3: telemetry/presentation only).

The dispatch seam (in :mod:`stackowl.pipeline.authz_compose`) calls these to compose
and enforce EFFECTIVE bounds. For non-seam callers, :func:`check_tool_bounds` is a
legacy owl-only convenience wrapper that delegates to effective_bounds/check_effective_bounds.

Relationship to consent: bounds are a HARD capability allowlist (the owl cannot
use the tool at all). Consent is human approval for a consequential tool. Bounds
are checked before consent/execution; a tool outside bounds is refused regardless
of consent.

FR35 DELEGATION — runtime floor wired in E2-S2: a delegated child is clamped to
the PARENT'S EFFECTIVE bounds (parent_owl ∩ parent_creation_ceiling) at every
child-spawn site (delegate_task, sessions_spawn, sessions_send) via
:func:`stackowl.pipeline.authz_compose.child_floor`, threaded through
:class:`stackowl.infra.trace.TraceContext`. This closes the TOCTOU-delegation
gap: a resumed parent whose owl was widened after creation still restricts its
delegated children to the persisted ceiling. The remaining Epic 3 FR35 work is
the manifest-layer parent_owl ∩ child_owl reconciliation (no-escalation when the
child manifest itself has wider bounds than the parent manifest).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.authz.bounds import BoundsSpec
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.owls.manifest import OwlAgentManifest


def effective_bounds(*specs: BoundsSpec | None) -> BoundsSpec | None:
    """Fold N optional bounds specs into one, narrowing-only.

    None terms are skipped (an absent constraint never widens). With no defined
    term the result is None (genuinely unbounded). Otherwise the defined terms
    are intersected left-to-right via BoundsSpec.intersect (TOOLS axis composed
    for real; other axes keep self, per S1). Total + narrowing: every defined
    term can only tighten. A SINGLE defined term is returned unchanged (identity)
    — the back-compat wrapper depends on this.
    """
    acc: BoundsSpec | None = None
    for spec in specs:
        if spec is None:
            continue
        acc = spec if acc is None else acc.intersect(spec)
    return acc


def check_effective_bounds(effective: BoundsSpec | None, tool_name: str) -> str | None:
    """Return a block-reason if effective bounds forbid the tool, else None.

    None effective bounds (no constraint anywhere) → unrestricted → None.
    """
    if effective is None or effective.permits_tool(tool_name):
        return None
    # THE REFUSAL CARRIES THE RECOVERY. This message used to say "choose one of its
    # permitted tools" without ever naming them, and never mentioned that
    # `delegate_task` is how an owl reaches a capability it does not hold. So the
    # model picked another tool it also lacked and was refused again. Measured
    # 2026-08-19: 315 bounds refusals across the owls (sysfup 141, headhunter 73,
    # Brain 53, sysdesign 39) with nothing anywhere repairing them — Bakir's "it is
    # continuously failing and does not have ability to re-heal himself".
    #
    # Everything needed was already in `effective` and simply was not passed on.
    # This is not a new mechanism: the refusal that already existed becomes
    # actionable.
    allowed = sorted(effective.tools or ())
    lines = [
        f"The action '{tool_name}' is not permitted by this owl's bounds and was "
        "not run."
    ]
    if allowed:
        # THE WHOLE LIST, never a truncation. BAKIR, 2026-08-19: "Agent should have
        # access to all list to choose." An abbreviated list is the same defect
        # this message exists to fix — an owl that cannot see a permitted tool
        # cannot choose it, and goes back to guessing. The text is longer on an
        # owl with many tools; that is the correct trade against another refusal.
        lines.append("This owl may use: " + ", ".join(allowed) + ".")
    if "delegate_task" in allowed:
        lines.append(
            f"To use '{tool_name}' anyway, delegate_task to an owl that holds it "
            "(the secretary) — state the exact action you need and use its result."
        )
    lines.append("Otherwise answer the user directly with what you have.")
    return " ".join(lines)


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
    # 3+4. Delegate to the shared combiner+checker. effective_bounds(single) is
    # identity, so this is byte-for-byte the prior owl-only verdict.
    block = check_effective_bounds(effective_bounds(owl_manifest.bounds), tool_name)
    if block is None:
        log.engine.debug(
            "[authz] bounds_guard.check: tool permitted by bounds",
            extra={"_fields": {"tool": tool_name, "owl": owl_manifest.name}},
        )
    else:
        log.engine.debug(
            "[authz] bounds_guard.check: tool outside owl bounds — blocking",
            extra={"_fields": {"tool": tool_name, "owl": owl_manifest.name, "axis": "tools"}},
        )
    return block
