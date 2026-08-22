"""authz_compose — resolve an owl's live bounds and compose effective bounds.

Lives in the PIPELINE layer (not authz) because it reads the OwlRegistry; the
pure narrowing math stays in authz.bounds_guard (no services import). The single
source of truth for "what bounds apply to this dispatch", reused by the dispatch
seam AND the delegation-floor at child-spawn sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.authz.bounds_guard import effective_bounds
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.authz.bounds import BoundsSpec
    from stackowl.owls.registry import OwlRegistry
    from stackowl.pipeline.state import PipelineState


def resolve_owl_bounds(owl_name: str, owl_registry: OwlRegistry | None) -> BoundsSpec | None:
    """Best-effort live bounds for an owl. None registry / unknown owl → None.

    A genuine lookup is attempted; an UNKNOWN owl (not registered) is treated as
    unbounded (None) — byte-for-byte S1 for unknown owls. This does NOT swallow
    arbitrary faults: OwlNotFoundError means "unknown owl"; any other exception
    propagates (the caller decides fail-closed).
    """
    if owl_registry is None:
        return None
    from stackowl.exceptions import OwlNotFoundError

    try:
        return owl_registry.get(owl_name).bounds
    except OwlNotFoundError:
        log.engine.debug(
            "[authz] compose.resolve: unknown owl — unbounded",
            extra={"_fields": {"owl": owl_name}},
        )
        return None


def child_floor(
    parent_owl_name: str,
    parent_creation_ceiling: BoundsSpec | None,
    owl_registry: OwlRegistry | None,
) -> BoundsSpec | None:
    """The ceiling a delegated child inherits: the parent's EFFECTIVE bounds =
    parent_owl(now) ∩ parent_creation_ceiling. Equals compute_effective_bounds of
    the parent's state (task_envelope is None in S2). Closes the TOCTOU-delegation
    gap: a resumed parent whose owl widened still clamps children to its persisted
    ceiling. When the parent has no ceiling, this is just the parent owl's bounds
    (the prior behavior)."""
    return effective_bounds(resolve_owl_bounds(parent_owl_name, owl_registry), parent_creation_ceiling)


def compute_effective_bounds(
    state: PipelineState, owl_registry: OwlRegistry | None
) -> BoundsSpec | None:
    """effective = owl.bounds(now) ∩ creation_ceiling.

    Fail-closed contract for the CALLER: a non-OwlNotFound exception propagates so
    the dispatch seam denies (never falls through on an error in a security path).
    A genuinely unbounded owl with no ceiling returns None (unrestricted) — S1.

    Note: task_envelope is intentionally excluded from enforcement (E2-S3). It is a
    least-privilege DEFAULT used for presentation + drift telemetry only; the hard
    boundary must not depend on an LLM-derived hint.
    """
    owl_bounds = resolve_owl_bounds(state.owl_name, owl_registry)
    # E2-S3 — enforcement is owl ∩ creation_ceiling. task_envelope is enforced
    # SEPARATELY at the dispatch seam (ESC-29, 2026-08-21) with its own refusal and
    # its own appeal, because "the plan omitted it" and "your owl may not" are
    # different situations needing different recoveries.
    composed = effective_bounds(owl_bounds, state.creation_ceiling)
    return _with_router_tools(composed, owl=state.owl_name)


def _with_router_tools(spec: BoundsSpec | None, *, owl: str) -> BoundsSpec | None:
    """Union the boundary-router tools into an owl's effective bounds.

    THE APPEAL PATH WAS CLOSED AT THE ONE SEAM THAT ENFORCES IT. `ROUTER_TOOLS`
    (owls/tool_presets.py) exists so an owl can always REACH `owl_build` to ask
    for authority and `owls_list` to name a delegation target. It was honoured by
    `builder.py` when an owl is CREATED and by `owl_build_authz.py` when a ceiling
    is minted — and never here, at dispatch, which is where the refusal actually
    happens. An owl created before those tools entered the set, or whose bounds
    were narrowed since, could therefore never ask for anything again.

    MEASURED on the live box, 2026-08-21: `mailbutler` refused `owl_build` twice
    and `owls_list` three times, with `bounds.tools` and `creation_ceiling.tools`
    both frozen at the same 7 entries since 2026-08-20 and no successful grant in
    the log at all. That is the defect the record already named once — "the tool by
    which an owl asks for authority was gated by the authority it lacked ... a
    ceiling that cannot be APPEALED is not a legitimate choice, because the
    operator's answer becomes unreachable rather than merely unsought" (0f1431e9).
    It was fixed for owl CREATION and left open for owl EXECUTION: an actuator
    wired on only some paths, which is the first of this programme's four shapes.

    THIS CONFERS NO POWER TO ACT, and that is why it is safe to union rather than
    a hole in the boundary. `tool_presets.py` says it plainly: `owl_build`'s grant
    action is gated on the `authority_widening` consent category, which is
    always-ask and stays always-ask. This lets an owl RAISE the question; only the
    operator answers it. `owls_list` is enumeration, not action.

    A `None` spec means genuinely unbounded — nothing to widen, so it is returned
    untouched rather than converted into a 5-tool allowlist, which would NARROW an
    unbounded owl and is the exact inversion this function must not perform.

    AN EMPTY ALLOWLIST IS LEFT ALONE, and an existing invariant lock is what caught
    that: `test_empty_allowlist_blocks_even_discovery_meta_tools` states that
    `tools=frozenset()` "denies ALL tools, INCLUDING the discovery meta-tools ...
    no auto-exemption — a documented builder-time concern". The first version of
    this fix broke it. The distinction is real and worth keeping: an EMPTY
    allowlist is an operator saying "this owl may do nothing", which is a complete
    and deliberate statement; a NON-EMPTY one is a working list that may simply
    predate `ROUTER_TOOLS`. Widening the first would be privilege escalation;
    widening the second restores an appeal the platform already promises.
    """
    if spec is None or spec.tools is None:
        return spec
    if not spec.tools:
        # Explicit total denial. Absolute — see the docstring.
        return spec
    from stackowl.owls.tool_presets import ROUTER_TOOLS

    missing = ROUTER_TOOLS - set(spec.tools)
    if not missing:
        return spec
    log.engine.info(
        "[authz] compose: boundary-router tools added to effective bounds so the "
        "owl can still ASK for authority",
        extra={"_fields": {"owl": owl, "added": sorted(missing)}},
    )
    return spec.model_copy(update={"tools": frozenset(spec.tools) | ROUTER_TOOLS})
