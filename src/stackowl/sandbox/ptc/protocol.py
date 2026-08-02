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

# ---------------------------------------------------------------- policy: denylist
#
# D05.5 — THE TRUST MODEL CHANGED HERE, DELIBERATELY, BY OPERATOR DECISION.
#
# This was a default-DENY allowlist of five names
# ({read_file, web_search, memory, write_file, edit}). It is now default-ALLOW
# minus :data:`PTC_DENYLIST`, matching the reference platform's shape.
#
# WHY: measured on 1,470 tool-using turns, 84% were multi-call chains (810 used
# 5+ calls, the longest 46) but only 20% fitted inside the five names — so PTC
# could collapse a fifth of the opportunity it was built for. The single largest
# blocker was web_fetch (4,102 appearances in chains) which was denied while its
# read-only sibling web_search was allowed.
#
# WHAT THIS COSTS, STATED PLAINLY RATHER THAN DISCOVERED LATER: sandboxed,
# LLM-authored code can now call every host tool except those below, INCLUDING
# the 13 consequential ones (send_message, owl_build, tool_build, skill_manage,
# send_file, browser_eval_js, …), and PTC does not re-prompt consent per call
# (see PtcToolInvoker.invoke). execute_code's own consent is therefore the ONLY
# consent covering everything a script does. The operator was shown this exact
# consequence and chose it.
#
# WHAT STILL HOLDS: bounds (owl ∩ creation_ceiling) are enforced per call by the
# invoker, every call is audited, write-confinement still applies to
# PTC_WRITE_TOOLS, and the rate-limit / arg-bound / frame caps are unchanged.
# The denylist below is no longer a safety fence around capability — it is
# strictly a SANDBOX-ESCAPE fence.
PTC_DENYLIST: frozenset[str] = frozenset(
    {
        # Arbitrary host command execution — the escape itself.
        "shell",
        # Recursion: a sandboxed run starting another sandboxed run.
        "execute_code",
        # Host process control.
        "process",
        # Spawns an external coding agent that runs OUTSIDE the sandbox with
        # host filesystem access — an escape wearing a different name.
        "claude_code",
        # Fork-bomb vectors. E8-S0 already caps delegation depth for the model's
        # own calls; a script must not be a way around that ceiling.
        "delegate_task",
        "sessions_spawn",
        "sessions_send",
    }
)

#: Retained for callers that still ask "is this callable?" by name. NO LONGER a
#: five-name allowlist — it is now derived, and is the complement of the denylist
#: over whatever the registry holds. Kept as a function rather than a frozenset
#: so it cannot go stale as tools are registered.
def ptc_callable(tool: str) -> bool:
    """Whether ``tool`` may be invoked from inside a sandbox (default-ALLOW)."""
    return bool(tool) and tool not in PTC_DENYLIST


# Back-compat shim: the old name, now meaning "the tools that are NOT escape
# vectors". Anything importing PTC_ALLOWLIST for a membership test still gets a
# correct answer via ptc_callable(); this constant remains only so the denied
# set can be quoted in refusal messages.
PTC_ALLOWLIST: frozenset[str] = frozenset()

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
