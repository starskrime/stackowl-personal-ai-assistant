"""Target-owl resolution for ``delegate_task``.

Resolves the specialist an owl should delegate a sub-task to, in priority order:

1. Explicit ``to_owl`` name — used verbatim when it exists in the registry.
2. ``role`` hint — the first non-caller owl whose ``role`` matches (Unicode
   case-fold equality; no hardcoded English keyword branching).
3. Default — the first non-caller specialist in the registry.

:func:`resolve_target` returns a :class:`TargetResolution` that distinguishes
an explicitly-named-but-missing owl (``target_not_found``) from a genuine
no-candidate situation (``unresolved``) — it never silently swaps an unknown
explicit target for a different owl.

:func:`resolve_target_owl` is a compatibility shim for existing callers that
have not yet migrated to the structured result; it unwraps ``.name``.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.owls.registry import OwlRegistry


@dataclass(frozen=True)
class TargetResolution:
    """Structured result from :func:`resolve_target`.

    Attributes:
        name:   The resolved owl name, or ``None`` when resolution failed.
        reason: ``None`` on success; ``"target_not_found"`` when an explicit
                ``to_owl`` was supplied but not in the registry;
                ``"unresolved"`` when no non-caller candidate exists at all.
    """

    name: str | None
    reason: str | None  # None=ok | "target_not_found" | "unresolved"


def resolve_target(
    *,
    registry: OwlRegistry | None,
    to_owl: str | None,
    role: str | None,
    caller: str,
) -> TargetResolution:
    """Resolve the delegation target; never silently swaps an explicit-but-missing owl.

    Distinguishes ``target_not_found`` (explicit ``to_owl`` named but absent)
    from ``unresolved`` (no non-caller candidate available).
    """
    if registry is None:
        log.tool.warning("delegate_task.resolve: no owl_registry — cannot resolve target")
        return TargetResolution(None, "unresolved")

    # 1. Explicit name wins when it exists; fail structurally when it does not.
    if to_owl:
        try:
            registry.get(to_owl)
            return TargetResolution(to_owl, None)  # exact slug — byte-identical legacy path
        except OwlNotFoundError:
            pass
        # S8 — consistency with the gateway's vocative routing: an explicit target
        # may be spoken in any case or as the human display_name ("Tony" → slug
        # "tony"). Match case-folded against both `name` and `display`; a unique
        # hit resolves, a token shared by >1 owl stays unresolved (never guess).
        wanted = unicodedata.normalize("NFC", to_owl).casefold()
        hits = {
            m.name
            for m in registry.list()
            if unicodedata.normalize("NFC", m.name).casefold() == wanted
            or unicodedata.normalize("NFC", m.display).casefold() == wanted
        }
        if len(hits) == 1:
            resolved = next(iter(hits))
            log.tool.info(
                "delegate_task.resolve: case/display-folded to_owl → slug",
                extra={"_fields": {"to_owl": to_owl, "resolved": resolved}},
            )
            return TargetResolution(resolved, None)
        log.tool.warning(
            "delegate_task.resolve: requested to_owl not found",
            extra={"_fields": {"to_owl": to_owl, "ambiguous": len(hits) > 1}},
        )
        return TargetResolution(None, "target_not_found")  # do NOT fall through

    candidates = [m for m in registry.list() if m.name != caller]

    # 2. Role hint — first non-caller owl whose role case-folds equal.
    if role:
        wanted = role.casefold()
        for manifest in candidates:
            if manifest.role.casefold() == wanted:
                return TargetResolution(manifest.name, None)
        log.tool.warning(
            "delegate_task.resolve: no owl matched role",
            extra={"_fields": {"role": role}},
        )

    # 3. Default — first available non-caller specialist.
    if candidates:
        return TargetResolution(candidates[0].name, None)

    log.tool.warning("delegate_task.resolve: no non-caller specialist available")
    return TargetResolution(None, "unresolved")


def resolve_target_owl(
    *,
    registry: OwlRegistry | None,
    to_owl: str | None,
    role: str | None,
    caller: str,
) -> str | None:
    """Compatibility shim — unwraps :func:`resolve_target` to a plain name.

    Existing callers (``delegate_task``, ``sessions_spawn``) continue to work
    unchanged. Migrate them to :func:`resolve_target` to gain structured error
    handling (T5).
    """
    return resolve_target(registry=registry, to_owl=to_owl, role=role, caller=caller).name
