"""Shared self-healing primitives.

Every long-lived I/O-bound resource in StackOwl (browser runtime, DB pool,
ModelProviders, LanceDB/Kuzu adapters, channel adapters, MCP server) implements
:class:`HealableResource` so failures can be detected, the resource recycled,
and the in-flight operation retried exactly once at the call site via
:func:`retry_once_on_dead_handle`.

This module is intentionally minimal: a protocol + two helpers. Each subsystem
provides its own ``ensure_available()`` body that knows how to reconnect or
restart itself.

The second helper is :func:`jittered` (D04.6) — the one place that decorrelates a
self-computed retry delay. It lives HERE rather than in a new module because
``infra/`` is the base layer every other layer may depend on (the same argument
``retry_ledger.py`` makes, citing ``infra/trace.py`` and ``infra/recovery_context``
as the two prior proofs), and because a delay is retry POLICY, which is what this
module already owns. It is a function returning a float: it owns no timer, no
queue and no ``next_attempt_at``, so it collapses six copies of one rule rather
than adding the second engine the standing rule forbids.
"""

from __future__ import annotations

import math
import random
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from stackowl.infra.observability import log

#: Jitter as a fraction of the computed delay: the wait lands in
#: ``[d, d * 1.25]``. Deliberately NOT a config field — a jitter fraction is a
#: correctness property of a distributed retry, not a preference, and an operator
#: who can set it to 0 re-creates the lockstep it exists to prevent. The
#: ``fraction=`` parameter is for TESTS (and for proving the change is reversible
#: byte-for-byte), not for a knob.
JITTER_FRACTION = 0.25

#: One process-wide source, seeded by the OS. NOT seeded per call from a counter:
#: this platform runs one loop in one process, so per-call reseeding would buy
#: nothing and would make the sequence reproducible in a way that defeats the
#: decorrelation.
_JITTER_RNG = random.Random()


def jittered(
    delay: float, *, fraction: float = JITTER_FRACTION, rng: random.Random | None = None,
) -> float:
    """``delay`` plus a random share of itself — ADDITIVE ONLY, never shorter.

    ``jittered(d) >= d`` is the invariant, and it is the reason this is safe to
    place near retry code at all. Several delays in this platform are durations a
    SERVER chose (``retry_actuator``'s ``retry_after + buffer``, the Telegram
    flood deadline, the breaker's ``open_for`` cooldown); a jitter that could
    subtract would re-earn the ~10-hour flood ban of 2026-07-19. Those sites are
    additionally guarded by
    ``tests/infra/test_jitter_can_only_ever_add.py`` — the invariant makes it
    safe, the guard keeps it deliberate.

    Fails closed to ``0.0`` on a delay that is negative, NaN or infinite: a bad
    delay upstream must not become an unbounded random wait here.
    """
    if not math.isfinite(delay) or delay <= 0.0:
        return 0.0
    source = rng if rng is not None else _JITTER_RNG
    return delay + source.uniform(0.0, fraction * delay)

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

    # ``remedy`` is DELIBERATELY NOT declared here (D05.3). It is optional: the
    # capability registry reads it with ``getattr(resource, "remedy", None)`` so
    # a resource that has nothing useful to say simply omits it, and none of the
    # ten existing implementers had to change to keep satisfying this protocol.
    # Declaring it would make every implementer's conformance depend on adding a
    # property whose honest value is usually None.
    #
    # Implement it when the subsystem knows a CONCRETE operator action — e.g.
    # the browser runtime knowing "sudo apt install libx11-xcb1" — because a
    # gated tool stays visible through tool_search and a remedy is what makes
    # that visibility actionable rather than merely informative.

    def register_on_recycled(self, cb: Callable[[], None]) -> None:
        """Register a sync callback fired whenever the resource is recycled.

        Dependents (e.g. session registries) use this to drop dead refs.
        Callbacks MUST be sync and side-effect-only (state mutation, dict
        clears). They run inside the resource's recovery path and must not
        raise — exceptions are suppressed and logged.
        """
        ...


#: Exception TYPES that are a dead handle by construction, no message reading
#: required. These are stdlib and cover the majority of real cases: a reset or
#: refused connection, a broken pipe, an unexpected EOF.
#:
#: ``ConnectionError`` already covers BrokenPipe / Reset / Refused / Aborted —
#: all four are its subclasses. Listed as the single base rather than
#: enumerated, so a stdlib addition to that family is covered automatically.
_DEAD_HANDLE_TYPES: tuple[type[BaseException], ...] = (
    ConnectionError,
    EOFError,
)

#: Optional third-party error types, probed lazily by (module, attribute) so a
#: missing optional dependency can never break classification. Same shape as
#: ``providers/_resilient_round._classify_transport_cause``, which is the
#: established pattern in this codebase for exactly this problem.
_OPTIONAL_DEAD_HANDLE_TYPES: tuple[tuple[str, str], ...] = (
    ("playwright._impl._errors", "TargetClosedError"),
    ("aiohttp", "ServerDisconnectedError"),
    ("aiohttp", "ClientConnectionError"),
    ("httpx", "RemoteProtocolError"),
    ("httpx", "ConnectError"),
    ("websockets.exceptions", "ConnectionClosedError"),
)


def _is_dead_handle_type(exc: BaseException) -> bool:
    """Structural check: is ``exc`` a dead handle by its TYPE alone?"""
    if isinstance(exc, _DEAD_HANDLE_TYPES):
        return True
    for module_name, attr in _OPTIONAL_DEAD_HANDLE_TYPES:
        try:
            import importlib

            candidate = getattr(importlib.import_module(module_name), attr, None)
        except Exception:  # noqa: BLE001 — optional dep absent; never crash classification
            continue
        if isinstance(candidate, type) and isinstance(exc, candidate):
            return True
    return False


def looks_like_dead_handle(
    exc: BaseException, markers: tuple[str, ...] = DEFAULT_DEAD_HANDLE_MARKERS
) -> bool:
    """True if ``exc`` is a dead-handle / dead-connection failure.

    ADR-19 obligation ① — SIGNALS ARE STRUCTURAL. This used to be nothing but
    ``any(m in str(exc) for m in markers)``: nineteen hardcoded English
    substrings, at the root of the platform's own self-healing primitive,
    governing the retry decision for EVERY tool (``tools/base.py``) and the whole
    browser stack. D02.6 refused to port exactly that design from the reference
    platform for provider errors; it was already here for resource errors.

    Why it matters concretely: the markers are English, so a non-English locale
    or a reworded SDK message silently stops healing — with no error, because a
    missed heal looks identical to "there was nothing to heal". Observed on
    2026-08-05: a shutdown-race raised ``sqlite3.ProgrammingError("Cannot
    operate on a closed database.")``, a string NOT in the markers, and healing
    only triggered because the chained ``ValueError("Connection closed")``
    happened to contain one that was. That is luck, not classification.

    TYPE FIRST, text last. The substring pass is kept because some libraries
    (notably Playwright) raise a generic ``Error`` carrying the only useful
    signal in its message — there is genuinely nothing else to read. But it is
    now the LAST RESORT rather than the whole mechanism, and every use of it is
    logged, so how much we still depend on the fragile path is a measurement
    instead of a guess.
    """
    if _is_dead_handle_type(exc):
        return True
    msg = str(exc)
    matched = next((m for m in markers if m in msg), None)
    if matched is None:
        return False
    # ADR-19 I6 — make the fragile path VISIBLE. If this line is common in the
    # logs, the type list above is missing something and the fix is to add the
    # type, not another string.
    log.infra.debug(
        "[resilience] dead handle matched by TEXT, not type — fragile path",
        extra={"_fields": {"exc_type": type(exc).__name__, "marker": matched}},
    )
    return True


async def retry_once_on_dead_handle[T](
    op: Callable[[], Awaitable[T]],
    resource: HealableResource,
    *,
    op_name: str,
    dead_markers: tuple[str, ...] = DEFAULT_DEAD_HANDLE_MARKERS,
    is_dead: Callable[[BaseException], bool] | None = None,
) -> T:
    """Run ``op``; on dead-handle errors, recycle ``resource`` and retry exactly once.

    ``op`` MUST re-acquire its own short-lived handles (context, page, cursor)
    on each call — it will be invoked up to twice and the first attempt's
    resources are presumed dead.

    Classification: when ``is_dead`` is supplied it is the SOLE arbiter of
    whether a failure is a dead handle (used by callers that classify on
    exception type / errorcode rather than English text, e.g. DbPool). Otherwise
    the substring ``dead_markers`` heuristic is used.

    Raises whatever ``op`` raises on a non-dead-handle error (no retry) or on
    the second attempt's failure (one retry max).
    """
    # 1. ENTRY
    log.infra.debug(
        "[resilience] retry_once.entry",
        extra={"_fields": {"op": op_name}},
    )

    def _classify(exc: BaseException) -> bool:
        if is_dead is not None:
            return is_dead(exc)
        return looks_like_dead_handle(exc, dead_markers)

    try:
        result = await op()
    except Exception as exc:
        if not _classify(exc):
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
