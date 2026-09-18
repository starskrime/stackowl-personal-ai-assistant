"""Spec 2.4 -- gateway/core IPC link identity: peer-PID + per-boot secret.

Two independent checks keep an impostor (any same-user process, e.g. a shell
child of core's own process tree) from displacing the real gateway<->core
link (measured incident, review-security.md M6):

1. ``authorize_peer`` -- the OS-verified PID of the connecting socket peer
   (``peer_pid``, Linux ``SO_PEERCRED`` only -- this repo's only verified/
   production platform) must equal the PID the gateway itself spawned via
   ``spawn_core``. Checked BEFORE any Hello is sent (the caller,
   ``_accept_core``), so an impostor gets zero protocol information.
2. ``verify_link_secret`` -- a per-boot random secret (``generate_link_secret``,
   minted once per gateway process, passed to every core spawn -- initial AND
   every crash-respawn -- via ``ENV_LINK_SECRET``) that core echoes back in its
   real ``HelloFrame``. ``os.execv`` inherits the environment unchanged, so a
   code-change restart (same PID, same secret) needs no special-casing.

Both checks fail CLOSED: an unverifiable peer (no ``SO_PEERCRED`` on this
platform, or a raised ``getsockopt``) is treated as unverified and refused,
never trusted. Windows named-pipe IPC and macOS peer-credential support are
explicit, disclosed scope boundaries -- see `deferred-work.md`.
"""

from __future__ import annotations

import os
import secrets
import socket
import struct
import sys
from typing import Protocol

from stackowl.infra.observability import log


class PeerCredSocket(Protocol):
    """Structural type for anything ``peer_pid`` can call ``getsockopt`` on.

    In PRODUCTION, ``FrameConnection.raw_socket`` never hands back a literal
    ``socket.socket`` -- ``asyncio.StreamWriter.get_extra_info("socket")``
    returns an ``asyncio.trsock.TransportSocket`` (a read-only proxy asyncio
    wraps the real fd in), which is NOT a ``socket.socket`` subclass but DOES
    implement ``getsockopt`` identically. A nominal ``isinstance(x,
    socket.socket)`` check would silently discard that real object and return
    ``None`` on every real connection -- exactly the regression a full
    ``asyncio.start_unix_server``/``open_unix_connection`` integration test
    caught (a raw ``socket.socketpair()``, used elsewhere in this module's own
    tests, does NOT go through this proxy and would not have shown it).
    """

    def getsockopt(self, level: int, optname: int, buflen: int) -> bytes: ...  # noqa: D102


#: The one env var name every core spawn (initial + every crash-respawn)
#: carries the per-boot link secret under. Named so it ends in ``_SECRET`` --
#: irrelevant to log redaction (env vars are never logged), but kept
#: consistent with every other ``*secret`` name in this story.
ENV_LINK_SECRET = "STACKOWL_CORE_LINK_SECRET"


def generate_link_secret() -> str:
    """Mint a fresh per-boot secret. Called ONCE per gateway process."""
    # 1. ENTRY
    log.gateway.debug("[ipc] link_auth.generate_link_secret: entry")
    # 2. STEP -- cryptographically-random, URL-safe (safe to pass via env).
    value = secrets.token_urlsafe(32)
    # 4. EXIT -- never logs the value itself.
    log.gateway.debug("[ipc] link_auth.generate_link_secret: exit")
    return value


def read_link_secret_from_env() -> str | None:
    """Core-side: read the secret this process was spawned with (or None)."""
    # 1. ENTRY
    log.ipc.debug("[ipc] link_auth.read_link_secret_from_env: entry")
    # 2. STEP -- a plain env read; absence is a legitimate state (e.g. tests).
    value = os.environ.get(ENV_LINK_SECRET)
    # 4. EXIT -- never logs the value itself, only whether one was found.
    log.ipc.debug(
        "[ipc] link_auth.read_link_secret_from_env: exit",
        extra={"_fields": {"present": value is not None}},
    )
    return value


def verify_link_secret(*, expected: str, presented: str | None) -> bool:
    """Constant-time compare. ``presented=None`` (no secret sent) always fails."""
    # 1. ENTRY -- never logs either value.
    log.gateway.debug(
        "[ipc] link_auth.verify_link_secret: entry",
        extra={"_fields": {"presented_is_none": presented is None}},
    )
    # 2. DECISION / 3. STEP -- constant-time compare guards against timing leaks.
    ok = presented is not None and secrets.compare_digest(expected, presented)
    # 4. EXIT
    log.gateway.debug(
        "[ipc] link_auth.verify_link_secret: exit", extra={"_fields": {"ok": ok}},
    )
    return ok


def authorize_peer(*, observed_pid: int | None, supervised_pid: int | None) -> bool:
    """Pure: does the connecting peer's PID match the core this gateway supervises?

    Fails CLOSED on either side being unknown (``None``) -- an unverifiable
    peer credential or no core spawned yet is never treated as a match.
    """
    # 1. ENTRY
    log.gateway.debug(
        "[ipc] link_auth.authorize_peer: entry",
        extra={"_fields": {
            "observed_pid": observed_pid, "supervised_pid": supervised_pid,
        }},
    )
    # 2. DECISION -- both known AND equal.
    ok = (
        observed_pid is not None
        and supervised_pid is not None
        and observed_pid == supervised_pid
    )
    # 4. EXIT
    log.gateway.debug(
        "[ipc] link_auth.authorize_peer: exit", extra={"_fields": {"ok": ok}},
    )
    return ok


def peer_pid(sock: PeerCredSocket | None) -> int | None:
    """The OS-verified PID of the process on the other end of ``sock``.

    Linux only (``SO_PEERCRED``) -- this repo's only verified/production
    platform (Spec 2.4 Boundaries). Returns ``None`` -- never guesses -- when
    the socket is missing, the platform lacks ``SO_PEERCRED`` (e.g. macOS,
    which uses a different, unverified-here option numbering), or the
    ``getsockopt`` call itself raises. The caller fails CLOSED on ``None``.

    ``sock`` accepts anything structurally ``getsockopt``-capable (see
    ``PeerCredSocket``) -- a real ``socket.socket`` (this module's own tests,
    a raw ``socket.socketpair()``) OR asyncio's ``TransportSocket`` proxy
    (what production actually passes, via ``FrameConnection.raw_socket``).
    """
    # 1. ENTRY
    log.gateway.debug("[ipc] link_auth.peer_pid: entry")
    if sock is None:
        log.gateway.warning(
            "[ipc] link_auth.peer_pid: no raw socket available — treating peer "
            "as unverified"
        )
        return None
    if not hasattr(socket, "SO_PEERCRED"):
        log.gateway.warning(
            "[ipc] link_auth.peer_pid: SO_PEERCRED unavailable on this platform "
            "(%s) — treating peer as unverified (fail-closed; see deferred-work.md)",
            sys.platform,
            extra={"_fields": {"platform": sys.platform}},
        )
        return None
    try:
        # struct ucred { pid_t pid; uid_t uid; gid_t gid; } -- three native ints.
        creds = sock.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        pid = int(struct.unpack("3i", creds)[0])
    except OSError as exc:
        log.gateway.warning(
            "[ipc] link_auth.peer_pid: getsockopt(SO_PEERCRED) raised — treating "
            "peer as unverified",
            exc_info=exc,
            extra={"_fields": {"platform": sys.platform}},
        )
        return None
    # 4. EXIT
    log.gateway.debug(
        "[ipc] link_auth.peer_pid: exit", extra={"_fields": {"pid": pid}},
    )
    return pid
