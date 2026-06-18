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
    # E2-S3 — enforcement is owl ∩ creation_ceiling ONLY. task_envelope is a
    # least-privilege DEFAULT used for presentation + drift telemetry, never for
    # enforcement (the hard boundary must not depend on an LLM-derived hint).
    return effective_bounds(owl_bounds, state.creation_ceiling)
