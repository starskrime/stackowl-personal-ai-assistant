"""PTC (Pellet-to-Capability) — the host-tool callback channel for sandboxed code.

This package opens a CONTROLLED, default-DENY hole through the sandbox boundary so
LLM-generated code running INSIDE the no-network sandbox can call back to a small,
CURATED allowlist of HOST tools over a per-run unix-domain socket. It is the
highest-risk component in the E11 roadmap: the HOST side (:class:`PtcServer`) is the
trust boundary and assumes the in-sandbox code is FULLY MALICIOUS.

The load-bearing guarantees (all enforced HOST-side; the sandbox is never trusted to
self-limit):

* **Default-ALLOW minus escape vectors** (D05.5; was a five-name default-DENY
  allowlist). Every host tool is callable EXCEPT :data:`PTC_DENYLIST` —
  ``shell``, ``execute_code``, ``process``, ``claude_code``, ``delegate_task``,
  ``sessions_spawn``, ``sessions_send`` — which are refused WITHOUT invoking
  anything. THIS INCLUDES CONSEQUENTIAL TOOLS, and PTC does not re-prompt consent
  per call: ``execute_code``'s own consent is the only consent covering a whole
  script. Changed by operator decision with that cost stated; the denylist is now
  a sandbox-ESCAPE fence, not a capability fence.
* **Write-confinement to the SANDBOX workspace** — ``write_file``/``edit`` may only
  touch paths resolving inside the run's own sandbox workspace, never the host
  project tree, ``~/.stackowl`` secrets, or the agent data_root.
* **Rate-limit + per-call timeout** — a bounded per-run call cap and a bounded
  per-call timeout so malicious code cannot spam/DoS the host.
* **Audit + never-leak** — every call is audited (tool name + bounded args, never
  secret values); a failure returns a sanitized structured error, never a host path
  or traceback; the socket is 0600 and unlinked on teardown.
* **Never-raise (B5)** — a PTC failure returns a structured error frame to the
  sandbox; it never crashes the run or the host. The mounting of the socket is the
  ONLY relaxation — the network stays denied and no other host FS is exposed.
"""

from __future__ import annotations

from stackowl.sandbox.ptc.protocol import (
    PTC_DENYLIST,
    PTC_SOCK_ENV,
    PtcLimits,
    in_sandbox_sock_path,
)
from stackowl.sandbox.ptc.server import PtcServer
from stackowl.sandbox.ptc.stub import render_stub

__all__ = [
    "PTC_DENYLIST",
    "PTC_SOCK_ENV",
    "PtcLimits",
    "PtcServer",
    "in_sandbox_sock_path",
    "render_stub",
]
