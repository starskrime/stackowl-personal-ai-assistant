"""PtcServer — the HOST-side trust boundary for the sandbox host-tool callback.

This is the load-bearing security component. It listens on a per-run unix-domain
socket (0600) that is the ONLY channel out of the otherwise no-network sandbox, and
for each framed request from the (UNTRUSTED, assumed-malicious) sandbox code it:

1. **Allowlist enforce (default-DENY, HOST-side).** Refuses any ``tool`` not in
   :data:`~stackowl.sandbox.ptc.protocol.PTC_ALLOWLIST` — ``shell``, ``execute_code``,
   ``process``, ``delegate_task``, every consequential tool — WITHOUT invoking
   anything. The sandbox is never trusted to self-limit.
2. **Rate-limit / arg bound.** A per-run call CAP (anti-DoS) and per-arg size bounds;
   past the cap, or an oversized arg, → refused.
3. **Write-confinement.** ``write_file``/``edit`` are re-anchored to the run's SANDBOX
   workspace (:mod:`stackowl.sandbox.ptc.confine`); a path escaping it is refused.
4. **Invoke + audit.** Runs the REAL host tool via the registry under the run's
   session/trace (reusing its path-guards), audits the call (tool + bounded args,
   never secret values), and frames a sanitized result/error back.

Never-raise (B5): every failure becomes a structured error FRAME; a handler exception
never crashes the run or the host, and the server keeps serving. Lifecycle: started
before the run, served concurrently during it, torn down + socket unlinked in a
``finally`` — see :meth:`aclose`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.sandbox.ptc.dispatch import PtcToolInvoker
from stackowl.sandbox.ptc.protocol import (
    PTC_ALLOWLIST,
    FrameError,
    PtcLimits,
    decode_request,
    encode_response,
)

__all__ = ["PtcServer"]

# 4-byte big-endian length prefix (mirrors protocol._LEN_PREFIX, read side).
_LEN_PREFIX_BYTES = 4


class PtcServer:
    """Per-run host-tool callback server (default-DENY allowlist; the trust boundary).

    Construct one PER sandbox run, bound to that run's scratch ``workspace`` and the
    HOST tool registry. ``start()`` binds a 0600 socket; ``aclose()`` tears it down +
    unlinks. Use as an async context manager around the sandbox launch.
    """

    def __init__(
        self,
        *,
        registry: object,
        workspace: Path,
        socket_path: Path,
        session_id: str = "",
        trace_id: str | None = None,
        audit_logger: object | None = None,
        limits: PtcLimits | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._session_id = session_id
        self._limits = limits if limits is not None else PtcLimits()
        self._clock = clock if clock is not None else WallClock()
        self._server: asyncio.AbstractServer | None = None
        self._call_count = 0
        self._lock = asyncio.Lock()
        # The invoker owns the actual host-tool crossing (bounds + confine + audit);
        # the server owns socket lifecycle + framing + allowlist/rate-limit policy.
        self._invoker = PtcToolInvoker(
            registry=registry,
            workspace=workspace,
            session_id=session_id,
            trace_id=trace_id,
            audit_logger=audit_logger,
            limits=self._limits,
        )

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    @property
    def call_count(self) -> int:
        return self._call_count

    # ----------------------------------------------------------------- lifecycle
    async def __aenter__(self) -> PtcServer:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def start(self) -> None:
        """Bind the per-run UDS at ``socket_path`` with 0600 perms. Never raises."""
        log.tool.debug(
            "[sandbox.ptc] start: entry",
            extra={"_fields": {"session": self._session_id or "-", "max_calls": self._limits.max_calls}},
        )
        # Remove any stale socket from a crashed prior run; bind under a tight umask so
        # the socket is created 0600 (no other user can connect to the host channel).
        with contextlib.suppress(OSError):
            if self._socket_path.exists():
                self._socket_path.unlink()
        prev_umask = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_conn, path=str(self._socket_path)
            )
        finally:
            os.umask(prev_umask)
        with contextlib.suppress(OSError):
            self._socket_path.chmod(0o600)
        log.tool.debug(
            "[sandbox.ptc] start: exit — listening",
            extra={"_fields": {"sock": str(self._socket_path)}},
        )

    async def aclose(self) -> None:
        """Stop serving, close the socket, unlink it. Never raises (B5)."""
        try:
            if self._server is not None:
                self._server.close()
                with contextlib.suppress(Exception):
                    await self._server.wait_closed()
                self._server = None
        finally:
            with contextlib.suppress(OSError):
                if self._socket_path.exists():
                    self._socket_path.unlink()
            log.tool.debug(
                "[sandbox.ptc] aclose: torn down",
                extra={"_fields": {"calls": self._call_count, "session": self._session_id or "-"}},
            )

    # ----------------------------------------------------------------- serving
    async def _handle_conn(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve framed requests on one connection until EOF. Never raises (B5)."""
        try:
            while True:
                frame = await self._read_frame(reader)
                if frame is None:
                    break
                response = await self._serve_one(frame)
                writer.write(response)
                await writer.drain()
        except (FrameError, ConnectionError, asyncio.IncompleteReadError) as exc:
            log.tool.debug("[sandbox.ptc] _handle_conn: connection ended", extra={"_fields": {"why": str(exc)}})
        except Exception as exc:  # B5 — a hostile peer must never crash the host.
            log.tool.error("[sandbox.ptc] _handle_conn: unexpected — closing conn", exc_info=exc)
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _read_frame(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read one length-prefixed frame; None on clean EOF. Bounds the length."""
        prefix = await reader.read(_LEN_PREFIX_BYTES)
        if not prefix:
            return None
        if len(prefix) < _LEN_PREFIX_BYTES:
            raise FrameError("truncated length prefix")
        length = int.from_bytes(prefix, "big")
        if length > self._limits.max_frame_bytes:
            # Reject BEFORE allocation — a hostile huge length can't exhaust memory.
            raise FrameError(f"frame length {length}B exceeds cap {self._limits.max_frame_bytes}B")
        return await reader.readexactly(length)

    async def _serve_one(self, frame: bytes) -> bytes:
        """Validate → enforce → invoke → audit → frame a response. Never raises."""
        try:
            req_id, tool, args = decode_request(frame)
        except FrameError as exc:
            return self._error(None, f"malformed request: {exc}")

        # 1. ENTRY — log SHAPE only (tool + arg KEY names), never arg VALUES (secrets).
        log.tool.info(
            "[sandbox.ptc] call: entry",
            extra={"_fields": {"tool": tool, "arg_keys": sorted(args.keys()), "session": self._session_id or "-"}},
        )

        # 2. DECISION (allowlist, default-DENY) — refuse anything not in the 5 names
        #    WITHOUT invoking anything. The sandbox is never trusted to self-limit.
        if tool not in PTC_ALLOWLIST:
            self._invoker.audit(tool, args, allowed=False, reason="not_allowlisted")
            log.tool.warning(
                "[sandbox.ptc] call: REFUSED (not allowlisted)",
                extra={"_fields": {"tool": tool}},
            )
            return self._error(req_id, f"tool '{tool}' is not callable from a sandbox")

        # Rate-limit: a per-run cap on callbacks (anti-spam / anti-DoS).
        async with self._lock:
            if self._call_count >= self._limits.max_calls:
                self._invoker.audit(tool, args, allowed=False, reason="rate_limited")
                log.tool.warning(
                    "[sandbox.ptc] call: REFUSED (rate cap)",
                    extra={"_fields": {"tool": tool, "cap": self._limits.max_calls}},
                )
                return self._error(
                    req_id,
                    f"sandbox host-tool call budget exhausted (cap {self._limits.max_calls})",
                )
            self._call_count += 1

        # Arg bounds — reject oversized args before doing any work.
        bound_err = self._invoker.check_arg_bounds(tool, args)
        if bound_err is not None:
            self._invoker.audit(tool, args, allowed=False, reason="arg_bounds")
            return self._error(req_id, bound_err)

        # 3. STEP — invoke the REAL host tool (write tools confined to the sandbox ws).
        try:
            output = await asyncio.wait_for(
                self._invoker.invoke(tool, args), timeout=self._limits.call_timeout_s
            )
        except TimeoutError:
            self._invoker.audit(tool, args, allowed=True, reason="timeout")
            log.tool.warning("[sandbox.ptc] call: timed out", extra={"_fields": {"tool": tool}})
            return self._error(req_id, f"host tool '{tool}' timed out")
        except Exception as exc:  # B5 — sanitized, never leak host paths/tracebacks.
            self._invoker.audit(tool, args, allowed=True, reason=f"error:{type(exc).__name__}")
            log.tool.error("[sandbox.ptc] call: tool raised", exc_info=exc, extra={"_fields": {"tool": tool}})
            return self._error(req_id, f"host tool '{tool}' failed ({type(exc).__name__})")

        # 4. EXIT — success path audited + framed back.
        self._invoker.audit(tool, args, allowed=True, reason="ok")
        log.tool.info(
            "[sandbox.ptc] call: exit",
            extra={"_fields": {"tool": tool, "ok": output.get("success"), "calls": self._call_count}},
        )
        if output.get("success"):
            return self._result(req_id, str(output.get("output", "")))
        return self._error(req_id, str(output.get("error") or "host tool reported failure"))

    # ----------------------------------------------------------------- framing
    def _result(self, req_id: object, output: str) -> bytes:
        """Frame a success result; a too-large output degrades to a clean error.

        A host tool can return more than ``max_frame_bytes`` (e.g. a big file read).
        Rather than raise, the over-cap result is reported to the sandbox as a
        structured "too large" error so the channel never breaks (B5).
        """
        try:
            return encode_response(
                req_id=req_id, result=output, max_frame_bytes=self._limits.max_frame_bytes
            )
        except FrameError:
            log.tool.warning("[sandbox.ptc] result over frame cap — refusing", extra={"_fields": {"len": len(output)}})
            return self._error(
                req_id, f"host tool result exceeds the {self._limits.max_frame_bytes}-byte channel cap"
            )

    def _error(self, req_id: object, message: str) -> bytes:
        return encode_response(req_id=req_id, error=message, max_frame_bytes=self._limits.max_frame_bytes)
