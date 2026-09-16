"""A local push-service stand-in (AC6): acks a Web Push POST the way a real
relay (FCM/APNs/Mozilla) would, on its own ephemeral HTTPS port, so
`push.py`'s `pywebpush`-based sender can be proven end-to-end without ever
calling a real push relay -- the same "local stub only, never the real
service" principle `telegram_bot.py`'s `TestTelegramBot` already applies to
Telegram (see `tests/test_telegram_bot.py`'s docstring).

Bound by IP literal (`127.0.0.1`), not a `.local` host name: real push
endpoints (FCM, APNs, Mozilla's autopush) are always plain HTTPS hosts, so
dialing this stand-in the same way -- by IP, over real TLS -- is a closer
proxy for "a real relay's HTTPS endpoint" than dialing by `.local` name
would be. `ca.py`'s `setup(..., ip_sans=[...])` mints the IP-SAN leaf cert
this needs (Story 1.3's one additive change to that module).
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
import tempfile
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web

from . import ca as ca_module

DEFAULT_INSTALL_NAME = "bridge-spike-push-stub.local"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class ReceivedPush:
    path: str
    headers: dict[str, str]
    body: bytes


class PushStub:
    """One ephemeral HTTPS listener on loopback. Every POST to `/push/*` is
    acked `201 Created` (matching the real Web Push protocol's success
    status) and recorded in `self.received`, in-process, for a caller in the
    SAME Python process (`check.py`) to inspect directly -- there is no
    result file and nothing is ever persisted, mirroring `results/*.json`
    being gitignored for the real checklist but going further: this stand-in
    writes nothing to disk at all.
    """

    def __init__(self, install_name: str = DEFAULT_INSTALL_NAME) -> None:
        self.install_name = install_name
        self.port = _free_port()
        self.ca_artifacts = ca_module.setup(install_name, ip_sans=[ipaddress.ip_address("127.0.0.1")])
        self.received: list[ReceivedPush] = []
        self.app = web.Application()
        self.app.router.add_post("/push/{subscription_id}", self._handle_push)
        self._runner: web.AppRunner | None = None

    @property
    def endpoint_base(self) -> str:
        return f"https://127.0.0.1:{self.port}/push"

    async def _handle_push(self, request: web.Request) -> web.Response:
        body = await request.read()
        self.received.append(
            ReceivedPush(path=request.path, headers=dict(request.headers), body=body)
        )
        # A real relay's success response to a Web Push POST is 201 Created.
        return web.Response(status=201)

    def _build_ssl_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        with tempfile.TemporaryDirectory(prefix="bridge-spike-push-stub-") as tmp_dir:
            cert_path = Path(tmp_dir) / "leaf-chain.pem"
            key_path = Path(tmp_dir) / "leaf-key.pem"
            cert_path.write_bytes(self.ca_artifacts.leaf_cert_pem + self.ca_artifacts.ca_cert_pem)
            key_path.write_bytes(self.ca_artifacts.leaf_key_pem)
            context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        return context

    async def start(self) -> None:
        ssl_context = self._build_ssl_context()
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host="127.0.0.1", port=self.port, ssl_context=ssl_context)
        await site.start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


__all__ = ["DEFAULT_INSTALL_NAME", "PushStub", "ReceivedPush"]
