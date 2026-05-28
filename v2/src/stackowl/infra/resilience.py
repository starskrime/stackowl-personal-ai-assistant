"""Shared self-healing primitives.

Every long-lived I/O-bound resource in StackOwl (browser runtime, DB pool,
ModelProviders, LanceDB/Kuzu adapters, channel adapters, MCP server) implements
:class:`HealableResource` so failures can be detected, the resource recycled,
and the in-flight operation retried exactly once at the call site via
:func:`retry_once_on_dead_handle`.

This module is intentionally minimal: a protocol + one helper. Each subsystem
provides its own ``ensure_available()`` body that knows how to reconnect or
restart itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from stackowl.infra.observability import log

DEFAULT_DEAD_HANDLE_MARKERS: tuple[str, ...] = (
    # Playwright / browser
    "Connection closed",
    "Target closed",
    "Browser closed",
    "Browser.new_context",
    # SQLite / DB pool
    "database is locked",
    "disk I/O error",
    "no such table",
    "unable to open database",
    # HTTP / network
    "Connection refused",
    "Connection reset",
    "ServerDisconnectedError",
    "RemoteProtocolError",
    "ConnectionClosedError",
    "EOF occurred",
    # Stdio / IPC
    "Pipe closed",
    "BrokenPipeError",
    "Broken pipe",
)


@runtime_checkable
class HealableResource(Protocol):
    """Anything that owns a process/connection/handle that can die mid-use."""

    @property
    def available(self) -> bool:
        ...

    @property
    def unavailable_reason(self) -> str | None:
        ...

    async def ensure_available(self) -> None:
        """Make the resource usable. Raise if it cannot be recovered."""
        ...

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """Register a sync callback fired whenever the resource is recycled.

        Dependents (e.g. session registries) use this to drop dead refs.
        Callbacks MUST be sync and side-effect-only (state mutation, dict
        clears). They run inside the resource's recovery path and must not
        raise — exceptions are suppressed and logged.
        """
        ...


def looks_like_dead_handle(
    exc: BaseException, markers: tuple[str, ...] = DEFAULT_DEAD_HANDLE_MARKERS
) -> bool:
    """True if ``exc`` looks like a dead-handle / dead-connection failure."""
    msg = str(exc)
    return any(m in msg for m in markers)


async def retry_once_on_dead_handle[T](
    op: Callable[[], Awaitable[T]],
    resource: HealableResource,
    *,
    op_name: str,
    dead_markers: tuple[str, ...] = DEFAULT_DEAD_HANDLE_MARKERS,
) -> T:
    """Run ``op``; on dead-handle errors, recycle ``resource`` and retry exactly once.

    ``op`` MUST re-acquire its own short-lived handles (context, page, cursor)
    on each call — it will be invoked up to twice and the first attempt's
    resources are presumed dead.

    Raises whatever ``op`` raises on a non-dead-handle error (no retry) or on
    the second attempt's failure (one retry max).
    """
    # 1. ENTRY
    log.infra.debug(
        "[resilience] retry_once.entry",
        extra={"_fields": {"op": op_name}},
    )
    try:
        result = await op()
    except Exception as exc:
        if not looks_like_dead_handle(exc, dead_markers):
            # 2. DECISION — not a dead-handle error; re-raise immediately
            log.infra.debug(
                "[resilience] retry_once.exit: non-dead-handle error, propagating",
                extra={"_fields": {"op": op_name, "exc_type": type(exc).__name__}},
            )
            raise
        # 3. STEP — dead handle detected; recycle + retry once
        log.infra.warning(
            "[resilience] retry_once: dead handle detected — recycling and retrying once",
            exc_info=exc,
            extra={"_fields": {"op": op_name, "reason": resource.unavailable_reason}},
        )
        await resource.ensure_available()
        result = await op()
    # 4. EXIT
    log.infra.debug("[resilience] retry_once.exit: success", extra={"_fields": {"op": op_name}})
    return result
