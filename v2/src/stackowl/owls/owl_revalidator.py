"""Boot-time re-clamp of agent-minted owls to ``bounds ∩ creation_ceiling``.

A CONSISTENCY belt-and-suspenders (partial write / hot reload / a bounded
creator), NOT anti-tamper. Every ``origin="agent"`` owl is re-narrowed to the
intersection of its current bounds and the creation ceiling that was captured
when it was minted. The pass is:

* FAIL-CLOSED — an agent owl with no ``creation_ceiling`` is a corruption/tamper
  signal (``owl_build`` ALWAYS persists a ceiling for agent owls); it is forced
  to a deny-all ``BoundsSpec(tools=frozenset())`` rather than left unbounded.
* FAIL-SAFE — each owl is processed in isolation; one bad owl never aborts the
  loop or crashes boot. Every failure path logs loudly and forces deny-all.
* IDEMPOTENT — re-running yields the same result (intersection is idempotent).

This mirrors the DNA-hydrator overlay shape: get → ``model_copy(update=...)`` →
``registry.replace``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.authz.bounds import BoundsSpec
from stackowl.authz.bounds_guard import effective_bounds
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.owls.registry import OwlRegistry

# The fail-closed posture: a present-but-empty allowlist denies ALL tools. This
# is deliberately distinct from ``None`` (unbounded) — see BoundsSpec docs.
_DENY_ALL = BoundsSpec(tools=frozenset())


def revalidate_agent_owls(registry: OwlRegistry) -> int:
    """Re-clamp every ``origin="agent"`` owl to ``bounds ∩ creation_ceiling``.

    Returns the count of owls whose bounds were re-clamped (changed). Human- and
    builtin-origin owls are skipped untouched. Never raises.
    """
    log.engine.debug("[owls] revalidate_agent_owls: entry")
    reclamped = 0
    for manifest in list(registry.all()):
        if manifest.origin != "agent":
            continue
        name = manifest.name
        try:
            if manifest.creation_ceiling is None:
                # FAIL CLOSED: agent owl with no ceiling is a corruption/tamper
                # signal (owl_build always persists one). Deny everything.
                log.engine.error(
                    "[owls] revalidate_agent_owls: agent owl has no creation_ceiling "
                    "— forcing deny-all bounds (fail-closed)",
                    extra={"_fields": {"owl": name, "created_by": manifest.created_by}},
                )
                if manifest.bounds != _DENY_ALL:
                    registry.replace(manifest.model_copy(update={"bounds": _DENY_ALL}))
                    reclamped += 1
                continue

            current = manifest.bounds if manifest.bounds is not None else BoundsSpec()
            clamped = effective_bounds(current, manifest.creation_ceiling)
            # effective_bounds of two defined specs is never None; guard anyway so
            # an agent owl can never end up unbounded.
            if clamped is None:
                clamped = _DENY_ALL
            if clamped.tools != (manifest.bounds.tools if manifest.bounds is not None else None):
                log.engine.info(
                    "[owls] revalidate_agent_owls: re-clamped agent owl bounds",
                    extra={"_fields": {
                        "owl": name,
                        "before": sorted(current.tools) if current.tools is not None else None,
                        "after": sorted(clamped.tools) if clamped.tools is not None else None,
                    }},
                )
                registry.replace(manifest.model_copy(update={"bounds": clamped}))
                reclamped += 1
        except Exception as exc:
            # FAIL SAFE: never let one bad owl abort the loop or crash boot.
            log.engine.error(
                "[owls] revalidate_agent_owls: re-clamp failed — forcing deny-all bounds",
                exc_info=exc,
                extra={"_fields": {"owl": name}},
            )
            try:
                registry.replace(manifest.model_copy(update={"bounds": _DENY_ALL}))
                reclamped += 1
            except Exception as exc2:
                # Even the fail-closed replace failed — log and keep going.
                log.engine.error(
                    "[owls] revalidate_agent_owls: deny-all replace also failed — skipping owl",
                    exc_info=exc2,
                    extra={"_fields": {"owl": name}},
                )
    log.engine.info(
        "[owls] revalidate_agent_owls: exit",
        extra={"_fields": {"reclamped": reclamped}},
    )
    return reclamped
