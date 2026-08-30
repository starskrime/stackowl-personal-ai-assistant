"""The two calls that make a memory plugin reach the model.

WHY A SEPARATE MODULE. `providers.py` owns the ABC and the registry; this owns the
two things the PIPELINE needs from them — what to present, and how to run a call.
Keeping them apart means `pipeline/steps/execute.py` imports a two-function
surface rather than the registry's whole vocabulary, and it gives the no-registry
and provider-raised cases one home instead of two inline try blocks on a hot path.

WHY THE SCHEMAS ARE APPENDED rather than competing inside the budgeted selection.
D08.2 settled that provider schemas are capped SEPARATELY at 6 and that the cap
"counts what plugins ADD rather than the total". Routing them through
`tool_count_cap` would let a busy turn silently drop a provider the operator
deliberately installed — the refuse-don't-truncate rule inverted. The bound is the
ceiling, enforced at activation, where a surplus provider is refused WHOLE.

LAW 1 HOLDS. The active set is frozen per incarnation by the registry, so these
schemas are byte-identical for the life of a conversation. A newly installed
plugin changes the array at the next incarnation, which is exactly where D01.1
permits the prompt to change.

LAW 2 HOLDS. The waist grows by at most the ceiling (6) however many providers
ship, and by ZERO when none are installed — which is today.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.memory.providers import MemoryProviderRegistry


def provider_schemas(registry: MemoryProviderRegistry | None) -> list[dict[str, Any]]:
    """Schemas the active memory providers contribute, or ``[]``.

    ``None`` is the ordinary state, not an error: no registry is wired on most
    paths and no provider is installed today, so this returns ``[]`` and the
    presented array is byte-identical to before.
    """
    if registry is None:
        return []
    try:
        schemas = registry.active_schemas()
    except Exception as exc:  # a provider must not break presentation
        log.memory.error(
            "[memory] provider_surface.provider_schemas: registry raised — "
            "presenting no provider tools this turn",
            exc_info=exc,
        )
        return []
    if schemas:
        log.memory.info(
            "[memory] provider_surface: memory-provider tools presented",
            extra={"_fields": {
                "count": len(schemas),
                "tools": [s.get("name") for s in schemas],
            }},
        )
    return schemas


async def dispatch_provider_tool(
    registry: MemoryProviderRegistry | None,
    tool_name: str,
    args: dict[str, Any],
) -> str | None:
    """Run ``tool_name`` on the provider that owns it, or return ``None``.

    ``None`` means NO PROVIDER OWNS THIS — distinct from "a provider ran it and
    said this". The caller needs that difference to fall through to its ordinary
    unknown-tool handling instead of replacing it with a memory-flavoured error.

    A provider that RAISES gets a failure string rather than propagating: it is
    untrusted code, it may fail its own call, and it may not take the turn with it.
    """
    if registry is None:
        return None
    try:
        provider = registry.provider_for(tool_name)
    except Exception as exc:
        log.memory.error(
            "[memory] provider_surface.dispatch: registry raised while routing",
            exc_info=exc, extra={"_fields": {"tool": tool_name}},
        )
        return None
    if provider is None:
        return None
    try:
        result = provider.handle_tool_call(tool_name, args)
        log.memory.info(
            "[memory] provider_surface: memory-provider tool ran",
            extra={"_fields": {"tool": tool_name, "provider": provider.name}},
        )
        return str(result)
    except Exception as exc:
        log.memory.error(
            "[memory] provider_surface: memory-provider tool FAILED — the turn "
            "continues without it",
            exc_info=exc,
            extra={"_fields": {"tool": tool_name}},
        )
        return f"The memory provider failed to run {tool_name}: {type(exc).__name__}."
