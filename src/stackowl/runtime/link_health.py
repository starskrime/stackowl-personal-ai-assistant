"""``GatewayCoreLinkHealthContributor`` -- surfaces a gateway/core Hello
mismatch in the health sweep. Mirrors ``journal/health.py``'s shape exactly.

State is process-global, module-level: a Hello mismatch is noticed on the
gateway's hot connection-accept path (``GatewayLink._route``'s HelloFrame
branch) and consulted from TWO independent readers that must see the SAME
counter -- ``GatewayLink.consecutive_hello_mismatches`` (which
``_supervise_core`` polls to decide whether to keep respawning the core) and
this module's own :class:`GatewayCoreLinkHealthContributor` (which the health
sweep polls). ``note_mismatch``/``note_match`` are the only writers.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.ipc.frames import HelloFrame

#: Spec 2.3 — the one operator remedy for every Hello mismatch: this story
#: deliberately does not build gateway self-restart (see the spec's Design
#: Notes / DW-11), so the only fix is a human-driven restart.
_REMEDY = (
    "gateway/core Hello mismatch — the gateway is almost always the stale "
    "side (it is the one process that runs for long stretches without "
    "reloading its own code); restart the gateway process to pick up "
    "matching code/schema/registry"
)


@dataclass
class LinkHealthState:
    """Tracks CONSECUTIVE Hello mismatches and the two payloads involved.

    A match resets the streak -- this answers "is the link failing RIGHT
    NOW", not "has it ever failed", matching ``JournalHealthState``'s own
    reasoning (D14.4's remedy contract).
    """

    consecutive_hello_mismatches: int = 0
    last_remedy: str | None = None
    last_local_summary: str | None = None
    last_remote_summary: str | None = None

    def note_mismatch(self, local_summary: str, remote_summary: str) -> None:
        self.consecutive_hello_mismatches += 1
        self.last_remedy = _REMEDY
        self.last_local_summary = local_summary
        self.last_remote_summary = remote_summary

    def note_match(self) -> None:
        self.consecutive_hello_mismatches = 0
        self.last_remedy = None
        self.last_local_summary = None
        self.last_remote_summary = None


_state = LinkHealthState()
_state_lock = threading.Lock()


def _summarize(hello: HelloFrame) -> str:
    return (
        f"protocol_version={hello.protocol_version} "
        f"highest_migration={hello.highest_migration} "
        f"registry_digest={hello.registry_digest} sender_pid={hello.sender_pid}"
    )


def note_mismatch(local: HelloFrame, remote: HelloFrame) -> None:
    """Record one Hello mismatch. Called only from ``GatewayLink._route``."""
    # 1. ENTRY
    log.gateway.debug(
        "[ipc] link_health.note_mismatch: entry",
        extra={"_fields": {"local_pid": local.sender_pid, "remote_pid": remote.sender_pid}},
    )
    # 2. STEP -- one counter increment under the lock.
    with _state_lock:
        _state.note_mismatch(_summarize(local), _summarize(remote))
        count = _state.consecutive_hello_mismatches
    # 4. EXIT
    log.gateway.debug(
        "[ipc] link_health.note_mismatch: exit",
        extra={"_fields": {"consecutive_hello_mismatches": count}},
    )


def note_match() -> None:
    """Record a confirmed-compatible Hello, resetting the mismatch streak."""
    # 1. ENTRY / 2. STEP -- one counter reset under the lock.
    log.gateway.debug("[ipc] link_health.note_match: entry")
    with _state_lock:
        _state.note_match()
    # 4. EXIT
    log.gateway.debug("[ipc] link_health.note_match: exit — mismatch streak cleared")


def _snapshot() -> LinkHealthState:
    with _state_lock:
        return LinkHealthState(
            consecutive_hello_mismatches=_state.consecutive_hello_mismatches,
            last_remedy=_state.last_remedy,
            last_local_summary=_state.last_local_summary,
            last_remote_summary=_state.last_remote_summary,
        )


def mismatch_count() -> int:
    """The current consecutive-mismatch streak -- the ONE counter both
    ``GatewayLink.consecutive_hello_mismatches`` and
    :class:`GatewayCoreLinkHealthContributor` read, so they can never disagree."""
    # 1. ENTRY -- DEBUG only: polled on the hot `_supervise_core` respawn loop.
    log.gateway.debug("[ipc] link_health.mismatch_count: entry")
    count = _snapshot().consecutive_hello_mismatches
    # 4. EXIT
    log.gateway.debug(
        "[ipc] link_health.mismatch_count: exit", extra={"_fields": {"count": count}},
    )
    return count


def reset_for_tests() -> None:
    """Clear the tracked state. Test-only -- the state is otherwise process-lifetime."""
    with _state_lock:
        _state.consecutive_hello_mismatches = 0
        _state.last_remedy = None
        _state.last_local_summary = None
        _state.last_remote_summary = None


class GatewayCoreLinkHealthContributor:
    """Health contributor: ``degraded`` while Hello mismatches are streaking.

    Never ``down`` -- Boundaries: this is deliberately the general health
    sweep's voice, not registered into the liveness/self-kill aggregator
    (``_build_liveness_aggregator``), so a stale-gateway condition is visible
    to an operator without also arming a process kill.
    """

    @property
    def contributor_name(self) -> str:
        return "gateway_core_link"

    async def health_check(self) -> HealthStatus:
        # 1. ENTRY
        log.gateway.debug("[ipc] GatewayCoreLinkHealthContributor.health_check: entry")
        snap = _snapshot()
        # 2. DECISION -- degraded while a mismatch streak is active, else ok.
        if snap.consecutive_hello_mismatches > 0:
            status = HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message=(
                    f"gateway/core Hello mismatch {snap.consecutive_hello_mismatches} "
                    f"time(s) in a row — local[{snap.last_local_summary}] "
                    f"remote[{snap.last_remote_summary}]"
                ),
                remedy=snap.last_remedy,
                latency_ms=0.0,
            )
        else:
            status = HealthStatus(
                name=self.contributor_name,
                status="ok",
                message=None,
                latency_ms=0.0,
            )
        # 4. EXIT
        log.gateway.debug(
            "[ipc] GatewayCoreLinkHealthContributor.health_check: exit",
            extra={"_fields": {"status": status.status}},
        )
        return status
