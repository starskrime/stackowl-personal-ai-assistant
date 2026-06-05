"""bounds_guard — pure bounds narrowing and effective-bounds checking (E2-S1/S2, FR33).

This module provides two core primitives:
- :func:`effective_bounds` — fold N optional bounds specs into one via intersection
  (narrowing-only composition of owl bounds ∩ creation_ceiling ∩ task_envelope)
- :func:`check_effective_bounds` — return a block-reason if effective bounds forbid
  a tool, or None when dispatch may proceed. Never raises — a tool outside bounds
  is *reported cleanly* (FR33: "stays within them and reports cleanly when blocked"),
  not crashed.

The dispatch seam (in :mod:`stackowl.pipeline.authz_compose`) calls these to compose
and enforce EFFECTIVE bounds. For non-seam callers, :func:`check_tool_bounds` is a
legacy owl-only convenience wrapper that delegates to effective_bounds/check_effective_bounds.

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
    return (
        f"The action '{tool_name}' is not permitted by this owl's bounds and was "
        "not run. This owl is restricted to a fixed set of tools; choose one of its "
        "permitted tools or answer the user directly."
    )


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
