"""PtcRunChannel — the per-run PTC lifecycle helper shared by the backends.

Both sandbox backends (bwrap, docker) wire the OPTIONAL PTC host-tool callback the
same way, so the start/stub-inject/serve/teardown choreography lives HERE (one place,
each backend's change stays minimal and the no-PTC path is untouched). Given a run's
sandbox workspace and a ``ptc_factory``, this:

1. writes the in-sandbox ``owl`` stub into the workspace (so user code can import it),
2. builds + starts the :class:`~stackowl.sandbox.ptc.server.PtcServer` on a per-run
   socket under the workspace (so the bind/volume mount can reach it), and
3. tears it down + unlinks the socket on :meth:`aclose` (always, in a ``finally``).

It is a NO-OP when ``ptc_factory`` is None — the backend then mounts nothing extra and
behaves byte-for-byte as before. Never raises on teardown (B5).
"""

from __future__ import annotations

import contextlib
import tempfile
import uuid
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.sandbox.base import PtcFactory
from stackowl.sandbox.ptc.server import PtcServer
from stackowl.sandbox.ptc.stub import render_stub

__all__ = ["PtcRunChannel"]

# The stub module filename written into the sandbox workspace (import owl).
_STUB_FILENAME = "owl.py"


class PtcRunChannel:
    """Owns one run's PTC server + injected stub. No-op when disabled.

    The HOST socket is bound under the system temp dir with a SHORT name (the
    ~/.stackowl scratch path is often too long for the ~108-byte AF_UNIX limit) and
    the backend bind/volume-mounts it to the fixed in-sandbox path. The stub is
    written into the (already-mounted) workspace so user code can ``import owl``.
    """

    def __init__(self, workspace: Path, ptc_factory: PtcFactory | None) -> None:
        self._workspace = workspace
        self._factory = ptc_factory
        self._server: PtcServer | None = None
        # A SHORT host socket path (respects AF_UNIX's ~108-byte limit regardless of
        # how deep ~/.stackowl/sandbox/<session> is). Bind-mounted into the sandbox.
        self._host_socket = Path(tempfile.gettempdir()) / f"owl-ptc-{uuid.uuid4().hex[:12]}.sock"

    @property
    def enabled(self) -> bool:
        return self._factory is not None

    @property
    def host_socket_path(self) -> Path:
        """The HOST path of the per-run socket (short; bind-mounted into the sandbox)."""
        return self._host_socket

    async def start(self) -> bool:
        """Inject the stub + start the server. Returns True iff PTC is now live.

        On ANY setup failure the channel self-disables (returns False) so the run
        still proceeds WITHOUT the callback rather than failing — the sandbox is no
        less isolated without PTC. Never raises (B5).
        """
        if self._factory is None:
            return False
        try:
            (self._workspace / _STUB_FILENAME).write_text(render_stub(), encoding="utf-8")
            server = self._factory(self._workspace, self._host_socket)
            await server.start()
            self._server = server
            log.tool.info(
                "[sandbox.ptc] channel started",
                extra={"_fields": {"sock": str(self._host_socket)}},
            )
            return True
        except Exception as exc:  # B5 — degrade to no-PTC, never break the run.
            log.tool.error("[sandbox.ptc] channel start failed — running without PTC", exc_info=exc)
            self._server = None
            return False

    async def aclose(self) -> None:
        """Tear down the server + unlink the socket + stub. Never raises (B5)."""
        if self._server is None:
            self._cleanup_files()
            return
        with contextlib.suppress(Exception):
            await self._server.aclose()
        self._server = None
        self._cleanup_files()

    def _cleanup_files(self) -> None:
        """Remove the injected stub + any stray host socket. Never raises."""
        with contextlib.suppress(OSError):
            stub = self._workspace / _STUB_FILENAME
            if stub.exists():
                stub.unlink()
        with contextlib.suppress(OSError):
            if self._host_socket.exists():
                self._host_socket.unlink()
