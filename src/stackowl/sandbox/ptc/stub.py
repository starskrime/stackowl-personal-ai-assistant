"""Stub generator — renders the in-sandbox ``owl.py`` module source.

The string returned by :func:`render_stub` is written into the run's sandbox
``workspace/`` so user code can ``import owl`` and call the allowlisted host tools
(``owl.read_file(...)``, ``owl.write_file(...)``, ``owl.edit(...)``,
``owl.web_search(...)``, ``owl.memory(...)``) over the per-run unix socket.

The stub is deliberately MINIMAL and DEPENDENCY-FREE — it runs INSIDE the sandbox
(stdlib ``socket``/``json``/``struct``/``os`` only; no stackowl import). It is pure
convenience: a clean client raise for a refused/failed call and a clean attribute
error for any non-allowlisted name (``owl.shell`` etc.). The REAL enforcement is
HOST-side and default-DENY in :class:`~stackowl.sandbox.ptc.server.PtcServer`; the
stub's client-side guard is courtesy, not a security control.

The socket path comes from the ``OWL_PTC_SOCK`` env var the backend injects (it is
NOT hardcoded here so the host stays the single source of truth for the path).
"""

from __future__ import annotations

from stackowl.sandbox.ptc.protocol import PTC_DENYLIST, PTC_SOCK_ENV

__all__ = ["render_stub"]

# The stub body. ``{denylist}`` / ``{sock_env}`` are filled from the host policy so
# the in-sandbox client mirrors the host's denylist + env var (one source of truth).
_STUB_TEMPLATE = '''\
"""owl — call HOST tools from inside the StackOwl sandbox.

Named helpers with friendly signatures: read_file, write_file, edit, web_search,
memory. EVERY other host tool is reachable generically by attribute, passing the
tool's own arguments as keywords:

    owl.web_fetch(url="https://example.com")
    owl.cronjob(action="list")

Chaining several calls in ONE script is the point: intermediate results never
enter the model's context, so a multi-step task costs one inference turn instead
of one per step.

A small set of SANDBOX-ESCAPE tools raises immediately (owl.shell,
owl.execute_code, owl.process, owl.claude_code, owl.delegate_task,
owl.sessions_spawn, owl.sessions_send). The host refuses them regardless of what
this module does — this is courtesy, not security.
"""
import json as _json
import os as _os
import socket as _socket
import struct as _struct

_DENYLIST = frozenset({denylist!r})
_SOCK_ENV = {sock_env!r}
_LEN = _struct.Struct(">I")
_MAX_FRAME = 1_048_576
_next_id = 0


class OwlToolError(RuntimeError):
    """A host tool refused, failed, or is not callable from the sandbox."""


def _sock_path():
    path = _os.environ.get(_SOCK_ENV)
    if not path:
        raise OwlToolError(
            "the host-tool channel is not available (OWL_PTC_SOCK unset)"
        )
    return path


def _call(tool, args):
    global _next_id
    _next_id += 1
    req = {{"id": _next_id, "tool": tool, "args": dict(args)}}
    body = _json.dumps(req).encode("utf-8")
    if len(body) > _MAX_FRAME:
        raise OwlToolError("request too large for the host-tool channel")
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        sock.connect(_sock_path())
        sock.sendall(_LEN.pack(len(body)) + body)
        prefix = _recvn(sock, 4)
        (length,) = _LEN.unpack(prefix)
        if length > _MAX_FRAME:
            raise OwlToolError("host-tool response too large")
        resp = _json.loads(_recvn(sock, length).decode("utf-8"))
    finally:
        sock.close()
    if "error" in resp and resp["error"] is not None:
        raise OwlToolError(resp["error"])
    return resp.get("result", "")


def _recvn(sock, n):
    chunks = []
    got = 0
    while got < n:
        chunk = sock.recv(n - got)
        if not chunk:
            raise OwlToolError("host-tool channel closed unexpectedly")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def read_file(path):
    """Read a file the host can see (host path-confinement applies). Returns text."""
    return _call("read_file", {{"path": path}})


def write_file(path, content):
    """Write a file INSIDE the sandbox workspace (host-confined). Returns status text."""
    return _call("write_file", {{"path": path, "content": content}})


def edit(path, old_string, new_string):
    """Edit a sandbox-workspace file (unique old->new replacement). Returns status."""
    return _call("edit", {{"path": path, "old_string": old_string, "new_string": new_string}})


def web_search(query, limit=5):
    """Search the web via the host provider cascade. Returns a JSON result string."""
    return _call("web_search", {{"query": query, "limit": limit}})


def memory(action, **kwargs):
    """Read/search durable host memory (action='search'|'get'|'add'|'forget')."""
    args = dict(kwargs)
    args["action"] = action
    return _call("memory", args)


def __getattr__(name):
    # D05.5 — default-ALLOW. Any host tool that is not a sandbox-escape vector is
    # reachable generically, with the tool's own arguments passed as keywords.
    # Private/dunder names are NOT tools; let those raise AttributeError normally
    # so `import owl` and introspection (copy, pickle, pytest) keep working
    # instead of being turned into an RPC call for `__wrapped__`.
    if name.startswith("_"):
        raise AttributeError(name)
    if name in _DENYLIST:
        raise OwlToolError(
            "tool %r is not callable from a sandbox (escape vector)" % name
        )

    def _generic(**kwargs):
        return _call(name, dict(kwargs))

    _generic.__name__ = name
    return _generic
'''


def render_stub() -> str:
    """Return the python source of the in-sandbox ``owl`` module (stdlib-only)."""
    return _STUB_TEMPLATE.format(
        denylist=sorted(PTC_DENYLIST), sock_env=PTC_SOCK_ENV
    )
