"""PTC (Pellet-to-Capability) — the host-tool callback channel for sandboxed code.

This package opens a CONTROLLED, default-DENY hole through the sandbox boundary so
LLM-generated code running INSIDE the no-network sandbox can call back to a small,
CURATED allowlist of HOST tools over a per-run unix-domain socket. It is the
highest-risk component in the E11 roadmap: the HOST side (:class:`PtcServer`) is the
trust boundary and assumes the in-sandbox code is FULLY MALICIOUS.

The load-bearing guarantees (all enforced HOST-side; the sandbox is never trusted to
self-limit):

* **Default-DENY allowlist** — ONLY the five names in :data:`PTC_ALLOWLIST`
  (``read_file``, ``web_search``, ``memory``, ``write_file``, ``edit``) are callable.
  Every other name — ``shell``, ``execute_code``, ``process``, ``delegate_task``, any
  consequential tool — is refused WITHOUT invoking anything.
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
    PTC_ALLOWLIST,
    PTC_SOCK_ENV,
    PtcLimits,
    in_sandbox_sock_path,
)
from stackowl.sandbox.ptc.server import PtcServer
from stackowl.sandbox.ptc.stub import render_stub

__all__ = [
    "PTC_ALLOWLIST",
    "PTC_SOCK_ENV",
    "PtcLimits",
    "PtcServer",
    "in_sandbox_sock_path",
    "render_stub",
]
