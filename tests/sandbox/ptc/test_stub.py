"""Stub tests — the in-sandbox ``owl`` module frames + parses correctly.

The stub source is rendered, exec'd in a throwaway namespace (NO real sandbox), and
pointed at a fake unix-socket server that echoes structured responses. Asserts the
stub frames a request the host can parse and surfaces result/error cleanly — and that
a non-allowlisted attribute (``owl.shell``) raises client-side without any socket.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import threading
from pathlib import Path

import pytest

from stackowl.sandbox.ptc.stub import render_stub

_LEN = struct.Struct(">I")


def _load_stub() -> dict[str, object]:
    ns: dict[str, object] = {}
    exec(compile(render_stub(), "owl.py", "exec"), ns)  # noqa: S102 — testing the generated module
    return ns


class _FakeHost:
    """A blocking unix-socket server that replies to ONE framed request, then stops."""

    def __init__(self, sock_path: Path, reply: dict[str, object]) -> None:
        self._path = sock_path
        self._reply = reply
        self.received: dict[str, object] | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import socket

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self._path))
        srv.listen(1)

        def _serve() -> None:
            conn, _ = srv.accept()
            with conn:
                prefix = conn.recv(4)
                (length,) = _LEN.unpack(prefix)
                body = b""
                while len(body) < length:
                    body += conn.recv(length - len(body))
                self.received = json.loads(body.decode())
                out = json.dumps(self._reply).encode()
                conn.sendall(_LEN.pack(len(out)) + out)
            srv.close()

        self._thread = threading.Thread(target=_serve, daemon=True)
        self._thread.start()

    def join(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=5.0)


async def _run_stub_call(ns: dict, fn: str, *args: object) -> object:
    """Run a (blocking) stub function off the event loop."""
    return await asyncio.to_thread(ns[fn], *args)


class TestStubFraming:
    async def test_read_file_round_trip(self, tmp_path: Path) -> None:
        sock = tmp_path / "ptc.sock"
        host = _FakeHost(sock, {"id": 1, "result": "file-contents"})
        host.start()
        ns = _load_stub()
        os.environ["OWL_PTC_SOCK"] = str(sock)
        try:
            result = await _run_stub_call(ns, "read_file", "notes.txt")
        finally:
            os.environ.pop("OWL_PTC_SOCK", None)
        host.join()
        assert result == "file-contents"
        # The host received a well-formed request naming the right tool + args.
        assert host.received is not None
        assert host.received["tool"] == "read_file"
        assert host.received["args"] == {"path": "notes.txt"}

    async def test_error_response_raises_owltoolerror(self, tmp_path: Path) -> None:
        sock = tmp_path / "ptc.sock"
        host = _FakeHost(sock, {"id": 1, "error": "tool 'x' is not callable from a sandbox"})
        host.start()
        ns = _load_stub()
        os.environ["OWL_PTC_SOCK"] = str(sock)
        try:
            with pytest.raises(Exception) as ei:  # OwlToolError (defined in the stub ns)
                await _run_stub_call(ns, "read_file", "x")
        finally:
            os.environ.pop("OWL_PTC_SOCK", None)
        host.join()
        assert "not callable" in str(ei.value)

    async def test_memory_packs_action(self, tmp_path: Path) -> None:
        sock = tmp_path / "ptc.sock"
        host = _FakeHost(sock, {"id": 1, "result": "[]"})
        host.start()
        ns = _load_stub()
        os.environ["OWL_PTC_SOCK"] = str(sock)
        try:
            await asyncio.to_thread(ns["memory"], "search", query="cats")
        finally:
            os.environ.pop("OWL_PTC_SOCK", None)
        host.join()
        assert host.received["tool"] == "memory"
        assert host.received["args"] == {"action": "search", "query": "cats"}


class TestStubClientGuards:
    def test_non_allowlisted_attribute_raises_cleanly(self) -> None:
        ns = _load_stub()
        getattr_fn = ns["__getattr__"]
        with pytest.raises(Exception) as ei:
            getattr_fn("shell")
        assert "not callable from a sandbox" in str(ei.value)

    def test_missing_env_raises_clean_error(self) -> None:
        ns = _load_stub()
        os.environ.pop("OWL_PTC_SOCK", None)
        with pytest.raises(Exception) as ei:
            ns["read_file"]("x")
        assert "not available" in str(ei.value) or "OWL_PTC_SOCK" in str(ei.value)
