"""Spec 2.3 -- the gateway/core Hello compatibility check.

``HelloFrame`` now travels BOTH directions and carries everything needed to
answer "do these two processes agree about the wire, the schema, and the
registered event types". ``evaluate_hello`` is the ONE pure, synchronous
function both sides run identically (over the SAME two Hello payloads) so
they agree independently -- no round-trip negotiation, no RPC.

Kept separate from ``startup.orchestrator``'s ``_core_frame_loop`` and
``runtime.gateway_link.GatewayLink._route`` so this compatibility LOGIC is
directly unit-testable without driving either the orchestrator or a live
socket.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from stackowl.infra.observability import log
from stackowl.ipc.frames import HelloFrame
from stackowl.journal.digest import compute_registry_digest

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.db.pool import DbPool


async def build_local_hello(
    *, sender_pid: int, db_pool: DbPool, link_secret: str | None = None,
) -> HelloFrame:
    """This process's own Hello -- freshly computed, never cached.

    AD-33: the gateway re-checks ``schema_head`` (here, the highest applied
    migration) and its registry digest on every accepted connection rather
    than caching either across core restarts, so a schema/registry change
    made WHILE this process keeps running is still detected on the next
    Hello exchange. ``highest_migration`` is read straight from
    ``schema_migrations`` -- zero-padded version text, parsed as ``int``;
    ``0`` when no migration has ever been applied (an empty/fresh database).

    Spec 2.4 -- ``link_secret`` is populated ONLY on core's real outgoing
    Hello (the caller passes ``runtime.link_auth.read_link_secret_from_env()``
    there); every other caller (the gateway's own Hello, `_core_frame_loop`'s
    local comparison-only Hello) leaves the default ``None`` -- `evaluate_hello`
    never compares this field, so a `None` here is harmless.
    """
    # 1. ENTRY -- never logs the secret itself, only whether one was passed.
    log.ipc.debug(
        "[ipc] hello.build_local_hello: entry",
        extra={"_fields": {
            "sender_pid": sender_pid, "link_secret_present": link_secret is not None,
        }},
    )
    # 2. STEP -- the one query; the row shape is the decision (present or empty).
    rows = await db_pool.fetch_all(
        "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
    )
    highest_migration = int(rows[0]["version"]) if rows else 0
    hello = HelloFrame(
        sender_pid=sender_pid,
        highest_migration=highest_migration,
        registry_digest=compute_registry_digest(),
        link_secret=link_secret,
    )
    # 4. EXIT
    log.ipc.debug(
        "[ipc] hello.build_local_hello: exit",
        extra={"_fields": {
            "sender_pid": sender_pid,
            "highest_migration": highest_migration,
            "registry_digest": hello.registry_digest,
        }},
    )
    return hello


@dataclass(frozen=True)
class HelloVerdict:
    """The outcome of comparing two Hello payloads.

    ``older`` is ``None`` when ``compatible`` is True. Otherwise it names
    WHICH of the two evaluated payloads (``"local"`` or ``"remote"``, from the
    calling side's own point of view) is the older one -- see
    :func:`evaluate_hello` for the ordering rule.
    """

    compatible: bool
    older: Literal["local", "remote"] | None


def evaluate_hello(
    *, local: HelloFrame, remote: HelloFrame, local_is_core: bool
) -> HelloVerdict:
    """Compare two Hello payloads and say whether the link is compatible.

    Pure, synchronous -- both the gateway and the core call this on the SAME
    two payloads (one each's own freshly-built Hello, the other's received
    over the wire) and reach the SAME verdict independently, with no
    negotiation. Ordering (Boundaries, Spec 2.3):

    1. Lower ``protocol_version`` is older.
    2. If equal, lower ``highest_migration`` is older.
    3. If both equal but ``registry_digest`` differs (no inherent ordering for
       a hash), the GATEWAY is conventionally treated as the older side: the
       core is always a freshly-spawned subprocess running current on-disk
       code (every initial spawn, every crash-respawn, every
       ``CodeWatcher``-triggered self-restart), while the gateway is the one
       process that can run for arbitrarily long stretches without ever
       reloading its own code -- so on a digest-only split, the gateway is the
       one holding stale in-memory registrations.
    """
    # 1. ENTRY
    log.ipc.debug(
        "[ipc] hello.evaluate_hello: entry",
        extra={"_fields": {
            "local_protocol_version": local.protocol_version,
            "remote_protocol_version": remote.protocol_version,
            "local_is_core": local_is_core,
        }},
    )
    # 2. DECISION -- protocol_version, then highest_migration, then digest.
    if local.protocol_version != remote.protocol_version:
        older: Literal["local", "remote"] = (
            "local" if local.protocol_version < remote.protocol_version else "remote"
        )
        verdict = HelloVerdict(compatible=False, older=older)
    elif local.highest_migration != remote.highest_migration:
        older = "local" if local.highest_migration < remote.highest_migration else "remote"
        verdict = HelloVerdict(compatible=False, older=older)
    elif local.registry_digest != remote.registry_digest:
        # The gateway is the conventionally-older side on a digest-only split.
        # `local_is_core=True` means `local` IS the core, so the GATEWAY is
        # `remote` from this call's point of view (and vice versa).
        older = "remote" if local_is_core else "local"
        verdict = HelloVerdict(compatible=False, older=older)
    else:
        verdict = HelloVerdict(compatible=True, older=None)
    # 4. EXIT
    log.ipc.debug(
        "[ipc] hello.evaluate_hello: exit",
        extra={"_fields": {"compatible": verdict.compatible, "older": verdict.older}},
    )
    return verdict


def react_to_core_hello_verdict(
    verdict: HelloVerdict,
    *,
    stop_event: asyncio.Event,
) -> None:
    """The CORE's pure/sync reaction to its own ``evaluate_hello`` verdict.

    EITHER side of a mismatch (``older == "local"`` -- the core is genuinely
    stale; ``older == "remote"`` -- the gateway is) sets ``stop_event``, the
    core's normal graceful-exit signal -- never ``restart_event``.

    ``restart_event`` would drive the EXISTING quiesce -> teardown ->
    ``os.execv`` self-restart, which replaces the process image WITHOUT the
    OS reporting an exit (same PID). ``_supervise_core``'s own
    ``_MAX_CONSECUTIVE_HELLO_MISMATCHES`` stand-down check only runs after
    ``_wait_for_exit_or_stall`` returns -- which an in-place ``os.execv``
    never triggers. A genuinely-older core would re-exec into the SAME
    still-old on-disk code, reconnect, mismatch again, and repeat --
    unbounded, with none of this story's retry-limit protection. Routing
    BOTH outcomes through ``stop_event`` means the process actually EXITS,
    which ``_supervise_core``'s crash-respawn loop DOES observe -- so its
    counter is the ONE place bounding every Hello-mismatch retry, exactly as
    intended.

    Compatible -- no-op.

    Kept separate from ``_core_frame_loop`` so this reaction is directly
    unit-testable without driving the orchestrator.
    """
    # 1. ENTRY
    log.ipc.debug(
        "[ipc] hello.react_to_core_hello_verdict: entry",
        extra={"_fields": {"compatible": verdict.compatible, "older": verdict.older}},
    )
    # 2. DECISION / 3. STEP -- any mismatch (either side) stops the core;
    # compatible is a no-op.
    if verdict.older in ("local", "remote"):
        stop_event.set()
    # 4. EXIT
    log.ipc.debug(
        "[ipc] hello.react_to_core_hello_verdict: exit",
        extra={"_fields": {"stop_event_set": stop_event.is_set()}},
    )
