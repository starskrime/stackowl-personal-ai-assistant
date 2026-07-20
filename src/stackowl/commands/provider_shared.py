"""Small primitives shared between /provider and /tier.

Both commands read/write the same ``providers:`` list in ``stackowl.yaml``
and both render a live circuit-breaker status badge next to a provider name
— factored out here once a second command needed the same two pieces of
bookkeeping ProviderCommand already had, rather than duplicating them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only; no runtime import cycle
    from stackowl.providers.registry import ProviderRegistry

__all__ = ["NO_STACKOWL_YAML", "live_status_badge", "providers_list"]

NO_STACKOWL_YAML = "No stackowl.yaml found — run stackowl setup --minimal first"


def providers_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the (live) providers list, normalising a missing/odd value."""
    raw = data.get("providers")
    if not isinstance(raw, list):
        raw = []
        data["providers"] = raw
    return raw


def live_status_badge(registry: ProviderRegistry | None, name: str) -> str:
    """Return a trailing ` [state]` badge for *name*, or "" when no live
    registry is wired (degrades gracefully — never crashes a caller)."""
    log.config.debug(
        "[commands] provider_shared.live_status_badge: entry", extra={"_fields": {"name": name}}
    )
    if registry is None:
        return ""
    breaker = registry.get_circuit_breaker(name)
    if breaker is None:
        log.config.debug(
            "[commands] provider_shared.live_status_badge: exit — no breaker",
            extra={"_fields": {"name": name}},
        )
        return " [no breaker]"
    from stackowl.providers.circuit_breaker import CircuitState

    state = breaker.state
    if state is CircuitState.CLOSED:
        badge = " [closed]"
    elif state is CircuitState.HALF_OPEN:
        badge = " [half-open]"
    else:
        badge = f" [open, retry in {breaker.retry_after_seconds:.0f}s]"
    log.config.debug(
        "[commands] provider_shared.live_status_badge: exit",
        extra={"_fields": {"name": name, "state": state.value}},
    )
    return badge
