"""Target-owl resolution for ``delegate_task``.

Resolves the specialist an owl should delegate a sub-task to, in priority order:

1. Explicit ``to_owl`` name — used verbatim when it exists in the registry.
2. ``role`` hint — the first non-caller owl whose ``role`` matches (Unicode
   case-fold equality; no hardcoded English keyword branching).
3. Default — the first non-caller specialist in the registry.

Returns ``None`` when nothing resolves (no registry, or only the caller is
registered) so the caller can surface a structured "unresolved target" refusal
rather than guessing. Pure function — no I/O, no raise — to keep
``delegate_task.py`` under the B2 line cap and isolate the resolution policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.exceptions import OwlNotFoundError
from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.owls.registry import OwlRegistry


def resolve_target_owl(
    *,
    registry: OwlRegistry | None,
    to_owl: str | None,
    role: str | None,
    caller: str,
) -> str | None:
    """Return the name of the specialist to delegate to, or ``None`` if unresolvable."""
    if registry is None:
        log.tool.warning("delegate_task.resolve: no owl_registry — cannot resolve target")
        return None

    # 1. Explicit name wins when it exists.
    if to_owl:
        try:
            registry.get(to_owl)
            return to_owl
        except OwlNotFoundError:
            log.tool.warning(
                "delegate_task.resolve: requested to_owl not found",
                extra={"_fields": {"to_owl": to_owl}},
            )

    candidates = [m for m in registry.list() if m.name != caller]

    # 2. Role hint — first non-caller owl whose role case-folds equal.
    if role:
        wanted = role.casefold()
        for manifest in candidates:
            if manifest.role.casefold() == wanted:
                return manifest.name
        log.tool.warning(
            "delegate_task.resolve: no owl matched role",
            extra={"_fields": {"role": role}},
        )

    # 3. Default — first available non-caller specialist.
    if candidates:
        return candidates[0].name

    log.tool.warning("delegate_task.resolve: no non-caller specialist available")
    return None
