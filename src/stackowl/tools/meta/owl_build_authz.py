"""owl_build authority core (no consent, no I/O). Pure security math — unit-testable
in isolation.

KEY SECURITY INSIGHT: the bounds clamp ``requested ∩ creator_floor`` is a NO-OP
when the creator is unbounded (an unbounded creator has bounds=None → floor is
None → intersection returns the request verbatim). So for an unbounded creator we
substitute a conservative ``SAFE_DEFAULT_CEILING`` (read-only-ish), forcing
consequential tools (shell/exec/write/network) to require explicit human widening
at consent. Authority (origin/created_by/creation_ceiling) is forced here
server-side; it is NEVER taken from the agent-supplied spec.
"""

from __future__ import annotations

from typing import get_args

from stackowl.authz.bounds import BoundsSpec
from stackowl.authz.bounds_guard import effective_bounds
from stackowl.infra.observability import log
from stackowl.owls.builder import OwlSpec, SpecialistOwlBuilder
from stackowl.owls.manifest import ModelTier, OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.tool_presets import ROUTER_TOOLS
from stackowl.pipeline.authz_compose import child_floor
from stackowl.tools.meta.owl_build_spec import OwlBuildSpec

# Read-only-ish: research/read + the discovery+delegation router tools. NO shell/
# exec/write/process/network. Must NOT be frozenset() — an empty allowlist denies
# the discovery meta-tools too (BoundsSpec footgun), stranding the owl.
SAFE_DEFAULT_CEILING = BoundsSpec(
    tools=frozenset({"read_file", "memory", "web_search", "web_fetch"}) | ROUTER_TOOLS
)

# Default tier when the spec omits one or supplies an invalid value (the builder
# would otherwise crash on a bad ModelTier). Never crash on None/garbage.
_DEFAULT_TIER: ModelTier = "standard"
_VALID_TIERS: frozenset[str] = frozenset(get_args(ModelTier))


def _coerce_tier(raw: str | None) -> ModelTier:
    """Map a free-form spec tier to a valid ModelTier, defaulting safely."""
    if raw in _VALID_TIERS:
        return raw  # type: ignore[return-value]  # membership-narrowed to a valid literal
    return _DEFAULT_TIER


def resolve_creation_ceiling(
    creator: str, parent_ceiling: BoundsSpec | None, registry: OwlRegistry
) -> BoundsSpec:
    """The creator's effective floor, or ``SAFE_DEFAULT_CEILING`` when unbounded.

    ``child_floor`` returns ``None`` exactly when the creator is unbounded AND has
    no creation_ceiling — the no-op case the safe default exists to close.
    """
    floor = child_floor(creator, parent_ceiling, registry)
    if floor is None:
        log.engine.debug(
            "[owl_build] authz.resolve_ceiling: unbounded creator — safe default",
            extra={"_fields": {"creator": creator}},
        )
        return SAFE_DEFAULT_CEILING
    return floor


def clamp_bounds(
    requested: BoundsSpec, ceiling: BoundsSpec
) -> tuple[BoundsSpec, frozenset[str]]:
    """Return ``(requested ∩ ceiling, dropped_tools)``. Narrowing-only."""
    clamped = effective_bounds(requested, ceiling)
    if clamped is None:  # both unbounded — should not happen (ceiling is concrete)
        clamped = BoundsSpec(tools=frozenset())
    req = requested.tools or frozenset()
    kept = clamped.tools or frozenset()
    dropped = req - kept
    if dropped:
        log.engine.debug(
            "[owl_build] authz.clamp: tools dropped above ceiling",
            extra={"_fields": {"dropped": sorted(dropped)}},
        )
    return clamped, dropped


def build_agent_manifest(
    spec: OwlBuildSpec,
    *,
    creator: str,
    parent_ceiling: BoundsSpec | None,
    registry: OwlRegistry,
    valid_tools: frozenset[str] | None = None,
) -> tuple[OwlAgentManifest, frozenset[str]]:
    """Build via :class:`SpecialistOwlBuilder`, then FORCE authority + clamp bounds.

    Returns ``(manifest, dropped_tools)``. Authority fields (origin/created_by/
    creation_ceiling) are stamped here, never read from the spec.

    ``valid_tools`` is the live ToolRegistry's tool-name catalog (the caller's
    job to compute — this module stays pure/no-I/O). The ONE production
    caller never passed this, so ``SpecialistOwlBuilder._validate`` always
    took its fail-open branch: a hallucinated/misspelled tool name in an
    agent-built owl's requested tools sailed through unvalidated, the ceiling
    clamp below narrows by AUTHORIZATION (what this creator may grant), not
    by whether the name is a real registered tool at all. ``None`` still
    degrades to the builder's existing fail-open warning (unchanged
    behavior for any caller that genuinely has no catalog to check against).
    """
    log.engine.debug(
        "[owl_build] authz.build: entry",
        extra={"_fields": {"name": spec.name, "preset": spec.preset, "creator": creator}},
    )
    specialty = spec.specialty or spec.name
    owl_spec = OwlSpec(
        name=spec.name,
        role=specialty,
        model_tier=_coerce_tier(spec.model_tier),
        preset=spec.preset,
        explicit_tools=tuple(spec.explicit_tools) if spec.explicit_tools else (),
        specialty=specialty,
        valid_tools=valid_tools,
    )
    built = SpecialistOwlBuilder().build(owl_spec)

    ceiling = resolve_creation_ceiling(creator, parent_ceiling, registry)
    # An unbounded built owl (no preset/explicit_tools) has bounds=None; treat that
    # as "requested everything" so the ceiling fully governs it (fail-closed).
    requested = built.bounds if built.bounds is not None else BoundsSpec()
    clamped, dropped = clamp_bounds(requested, ceiling)

    update: dict[str, object] = {
        "bounds": clamped,
        "tools": sorted(clamped.tools) if clamped.tools is not None else [],
        "origin": "agent",
        "created_by": creator,
        "creation_ceiling": ceiling,
    }
    # Schedule slot (TS8): a present cadence makes this a SCHEDULED persona woken by a
    # CronTrigger that runs its recurring goal each tick. Cadence + interval floor were
    # already validated upstream (validate_owl_build_spec); the goal defaults to the
    # standing specialty. No cadence ⇒ no trigger ⇒ on_demand (byte-identical default).
    schedule = (spec.schedule or "").strip()
    if schedule:
        from stackowl.owls.trigger import CronTrigger, ReportTrigger

        update["lifecycle"] = "scheduled"
        if spec.report is not None:
            update["trigger"] = ReportTrigger(report=spec.report, schedule=schedule)
        else:
            goal = (spec.goal or "").strip() or specialty
            update["trigger"] = CronTrigger(schedule=schedule, prompt=goal)
        log.engine.debug(
            "[owl_build] authz.build: scheduled trigger attached",
            extra={"_fields": {"name": spec.name, "schedule": schedule}},
        )
    # Design decisions 3 & 4 — carry the guardrail + evolution preset onto the
    # manifest (single path: both chat and /owl create reach this one forge).
    boundaries = (spec.boundaries or "").strip()
    if boundaries:
        update["boundaries"] = boundaries
    if spec.evolution_strategy is not None:
        update["evolution_strategy"] = spec.evolution_strategy
    manifest = built.model_copy(update=update)
    log.engine.debug(
        "[owl_build] authz.build: exit",
        extra={"_fields": {
            "name": manifest.name,
            "kept": len(clamped.tools or frozenset()),
            "dropped": sorted(dropped),
        }},
    )
    return manifest, dropped
