"""PTC wire protocol + policy constants — shared by the host server and the stub.

This module is PURE and stdlib-only (no stackowl imports) so the in-sandbox stub
(:mod:`stackowl.sandbox.ptc.stub` renders its source) can reuse the exact same
framing without dragging the host package into the sandbox. It defines:

* the framed request/response wire format (a 4-byte big-endian length prefix +
  a UTF-8 JSON body, bounded both directions so neither side can be flooded), and
* the SECURITY POLICY constants (the default-DENY allowlist, the write-confined
  subset, the per-run call cap, the per-call timeout, and the argument-size bounds).

Nothing here trusts the peer: the bounds are enforced on READ (a frame claiming a
huge length is rejected before allocation), and the allowlist is consulted HOST-side
in :class:`~stackowl.sandbox.ptc.server.PtcServer` — these constants are the single
source of truth both sides agree on, but only the host's enforcement is load-bearing.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

__all__ = [
    "PTC_ALLOWLIST",
    "PTC_SOCK_ENV",
    "PTC_WRITE_TOOLS",
    "FrameError",
    "PtcLimits",
    "decode_request",
    "encode_response",
    "in_sandbox_sock_path",
    "pack_frame",
]

# ---------------------------------------------------------------- policy: allowlist
# The ONLY host tools callable from inside a sandbox (default-DENY: everything not
# here is refused without invoking anything). Quoted in the server's refusal message.
PTC_ALLOWLIST: frozenset[str] = frozenset(
    {"read_file", "web_search", "memory", "write_file", "edit"}
)

# The subset whose target path MUST be confined to the run's sandbox workspace (never
# the host project tree / ~/.stackowl secrets / agent data_root). read_file uses its
# own normal workspace confinement; these two are re-anchored to the sandbox scratch.
PTC_WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit"})

# Env var the backend sets inside the sandbox; the stub reads it to find the socket.
PTC_SOCK_ENV = "OWL_PTC_SOCK"

# The in-sandbox path the per-run socket is bind-mounted at (a dotfile in the run's
# only writable mount). Returned by in_sandbox_sock_path() so callers don't hardcode.
_DEFAULT_IN_SANDBOX_SOCK = "/workspace/.ptc.sock"


def in_sandbox_sock_path(workspace_mount: str = "/workspace") -> str:
    """The socket path AS SEEN inside the sandbox (under the writable mount)."""
    return f"{workspace_mount.rstrip('/')}/.ptc.sock"


@dataclass(frozen=True)
class PtcLimits:
    """Bounded, mandatory rails for one PTC-enabled run (anti-spam / anti-DoS).

    ``max_calls`` caps the TOTAL number of host-tool callbacks one run may make;
    ``call_timeout_s`` bounds each individual call; the ``max_*`` byte bounds reject
    oversized arguments/frames before they are processed. Every value is a ceiling —
    a malicious payload cannot raise them (they live HOST-side).
    """

    max_calls: int = 64
    call_timeout_s: float = 10.0
    # Hard ceiling on a single wire frame (request OR response), bytes. Rejected on
    # read BEFORE allocation so a huge length-prefix cannot exhaust host memory.
    max_frame_bytes: int = 1_048_576  # 1 MiB
    # Per-argument-string ceiling (e.g. file content, a query). Oversized → refused.
    max_arg_bytes: int = 262_144  # 256 KiB
    # Query/text length bound for web_search / memory (chars).
    max_query_chars: int = 4_096


# ---------------------------------------------------------------- wire framing
# 4-byte big-endian unsigned length prefix precedes every JSON body.
_LEN_PREFIX = struct.Struct(">I")


class FrameError(Exception):
    """A malformed / oversized / truncated frame (the peer is not trusted)."""


def pack_frame(payload: dict[str, object], *, max_frame_bytes: int) -> bytes:
    """Encode ``payload`` as a length-prefixed UTF-8 JSON frame.

    Raises :class:`FrameError` if the encoded body exceeds ``max_frame_bytes`` (so a
    server never emits a frame the bounded reader on the other side would reject).
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > max_frame_bytes:
        raise FrameError(f"frame body {len(body)}B exceeds cap {max_frame_bytes}B")
    return _LEN_PREFIX.pack(len(body)) + body


def encode_response(
    *, req_id: object, result: str | None = None, error: str | None = None,
    max_frame_bytes: int,
) -> bytes:
    """Frame a response carrying EITHER a ``result`` or a sanitized ``error``."""
    payload: dict[str, object] = {"id": req_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result if result is not None else ""
    return pack_frame(payload, max_frame_bytes=max_frame_bytes)


def decode_request(body: bytes) -> tuple[object, str, dict[str, object]]:
    """Parse a request frame body into ``(id, tool, args)``. Never trusts the peer.

    Raises :class:`FrameError` on any shape violation (not JSON, not an object,
    missing/!str ``tool``, non-object ``args``) so a hostile payload is rejected with
    a structured error rather than crashing the handler.
    """
    try:
        obj = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameError(f"request is not valid UTF-8 JSON ({type(exc).__name__})") from exc
    if not isinstance(obj, dict):
        raise FrameError("request must be a JSON object")
    tool = obj.get("tool")
    if not isinstance(tool, str) or not tool:
        raise FrameError("request 'tool' must be a non-empty string")
    args = obj.get("args", {})
    if not isinstance(args, dict):
        raise FrameError("request 'args' must be a JSON object")
    return obj.get("id"), tool, args
