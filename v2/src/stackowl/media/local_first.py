"""LocalFirstSelector — the ONE local-first-then-cloud media backend policy.

CFG-4 (F019): image, tts (and any future modality) share an identical control
flow — prefer the self-hosted backend, fall back to cloud only when the local
one is unavailable AND the engine setting permits it ('auto'), skip local
entirely for engine='cloud', and return a structured, ACTIONABLE "unavailable"
(never raise) when nothing can run. That policy lived in three near-clone
selectors; it now lives here once, tested once. Per-modality concerns — the
concrete backend classes, the probe (and any args like a TTS voice), and the
unavailable-message wording — stay with the caller, supplied as factories.

The availability objects are duck-typed (:class:`_Availability` protocol): any
result exposing ``available: bool`` and ``reason: str | None`` works, so the
existing ``ImageAvailability`` / ``TtsAvailability`` plug in unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from stackowl.infra.observability import log


class _Availability(Protocol):
    # Read-only properties (not bare attributes) so a frozen dataclass like
    # ImageAvailability / TtsAvailability matches structurally without mypy
    # invariance complaints on the Callable return type.
    @property
    def available(self) -> bool: ...

    @property
    def reason(self) -> str | None: ...


@dataclass(frozen=True)
class LocalFirstSelection[BackendT]:
    """Outcome of local-first selection (mirrors the per-modality Selection types).

    Exactly one of ``backend`` (available) or ``reason`` (unavailable) is set.
    ``is_local`` discloses whether the chosen backend is self-hosted (no egress).
    """

    backend: BackendT | None
    is_local: bool
    reason: str | None

    @property
    def available(self) -> bool:
        return self.backend is not None


async def select_local_first[BackendT](
    *,
    engine: str,
    local_probe: Callable[[], Awaitable[_Availability]],
    cloud_probe: Callable[[], Awaitable[_Availability]],
    local_factory: Callable[[], BackendT],
    cloud_factory: Callable[[], BackendT],
    unavailable: Callable[[str, str], str],
    local_only_engine_reason: str = "cloud fallback disabled (engine is local-only)",
) -> LocalFirstSelection[BackendT]:
    """Select a backend local-first; structured-unavailable when none run. Never raises.

    Args:
        engine: ``'auto'`` (local then cloud fallback), ``'cloud'`` (skip local,
            cloud-only), or any other value = local-only (cloud never used).
        local_probe / cloud_probe: zero-arg awaitables returning an availability
            object (``.available`` / ``.reason``). The caller closes over any
            per-modality args (e.g. a TTS voice).
        local_factory / cloud_factory: build the chosen backend on demand.
        unavailable: builds the actionable all-unavailable message from
            ``(local_reason, cloud_reason)``.
        local_only_engine_reason: cloud-reason text when engine is local-only.

    4-point logging: entry / decision / step / exit.
    """
    log.tool.debug(
        "[media.local_first] select: entry", extra={"_fields": {"engine": engine}}
    )

    # engine='cloud' → skip the local probe entirely, go straight to cloud.
    if engine == "cloud":
        cloud_avail = await cloud_probe()
        if cloud_avail.available:
            log.tool.info("[media.local_first] select: chose CLOUD (engine='cloud', egress)")
            return LocalFirstSelection(backend=cloud_factory(), is_local=False, reason=None)
        cloud_reason = cloud_avail.reason or "cloud backend unavailable"
        log.tool.info("[media.local_first] select: cloud-only engine but cloud unavailable")
        return LocalFirstSelection(
            backend=None,
            is_local=False,
            reason=unavailable("(local skipped: engine='cloud')", cloud_reason),
        )

    # LOCAL-FIRST — prefer the self-hosted backend (data stays on the box).
    local_avail = await local_probe()
    if local_avail.available:
        log.tool.info("[media.local_first] select: chose LOCAL backend")
        return LocalFirstSelection(backend=local_factory(), is_local=True, reason=None)
    local_reason = local_avail.reason or "local backend unavailable"
    log.tool.info(
        "[media.local_first] select: local unavailable",
        extra={"_fields": {"reason": local_reason}},
    )

    # CLOUD FALLBACK — opt-in only, and only when engine='auto'.
    if engine == "auto":
        cloud_avail = await cloud_probe()
        if cloud_avail.available:
            log.tool.info("[media.local_first] select: chose CLOUD fallback (egress)")
            return LocalFirstSelection(backend=cloud_factory(), is_local=False, reason=None)
        cloud_reason = cloud_avail.reason or "cloud backend unavailable"
    else:
        cloud_reason = local_only_engine_reason

    log.tool.info("[media.local_first] select: no backend available")
    return LocalFirstSelection(
        backend=None, is_local=False, reason=unavailable(local_reason, cloud_reason)
    )
