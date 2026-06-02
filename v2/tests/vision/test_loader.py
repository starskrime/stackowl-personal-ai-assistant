"""E10-S1 — ImageLoader: local path (confined), URL via SsrfGuard, caps, MIME.

NO real network: ``httpx.AsyncClient.stream`` is monkeypatched to a fake context
manager. The SsrfGuard's DNS is made deterministic via an injected resolver.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from stackowl.infra.net.ssrf_guard import SsrfGuard
from stackowl.vision.loader import ImageLoader, LoadedImage, LoadError

_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


# --------------------------------------------------------------------------- #
# Workspace setup — point STACKOWL_DATA_DIR at a tmp dir so path_guard confines there.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(ws))
    return ws


# --------------------------------------------------------------------------- #
# httpx.AsyncClient.stream fake
# --------------------------------------------------------------------------- #
def _patch_stream(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: bytes,
    content_type: str,
    status: int = 200,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    class _Ctx:
        async def __aenter__(self) -> _Ctx:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        @property
        def headers(self) -> dict[str, str]:
            return {"content-type": content_type}

        def raise_for_status(self) -> None:
            if status >= 400:
                req = httpx.Request("GET", captured.get("url", "http://x/"))
                resp = httpx.Response(status, request=req)
                raise httpx.HTTPStatusError("err", request=req, response=resp)

        async def aiter_bytes(self) -> Any:
            yield body

    def fake_stream(self: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> _Ctx:
        captured["url"] = url
        # The AsyncClient's redirect policy lives on the public ``follow_redirects``
        # attribute — capture it so a test can assert the SSRF-protective False.
        captured["follow_redirects"] = self.follow_redirects
        return _Ctx()

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    return captured


def _public_guard() -> SsrfGuard:
    """A guard whose DNS always resolves to a public IP (deterministic, no real DNS)."""
    return SsrfGuard(resolve_fn=lambda host: ["93.184.216.34"])


# --------------------------------------------------------------------------- #
# Local path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_local_image_returns_bytes_and_mime(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img = workspace / "pic.png"
    img.write_bytes(_PNG)
    result = await ImageLoader().load("pic.png")
    assert isinstance(result, LoadedImage)
    assert result.data == _PNG
    assert result.media_type == "image/png"


@pytest.mark.asyncio
async def test_local_path_outside_workspace_refused(
    workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG)
    result = await ImageLoader().load(str(outside))
    assert isinstance(result, LoadError)
    assert "workspace" in result.reason.lower()


@pytest.mark.asyncio
async def test_local_non_image_refused(workspace: Path) -> None:
    f = workspace / "notimage.txt"
    f.write_bytes(b"just text, no image signature")
    result = await ImageLoader().load("notimage.txt")
    assert isinstance(result, LoadError)
    assert "image" in result.reason.lower()


@pytest.mark.asyncio
async def test_local_missing_file(workspace: Path) -> None:
    result = await ImageLoader().load("nope.png")
    assert isinstance(result, LoadError)
    assert "not found" in result.reason.lower()


# --------------------------------------------------------------------------- #
# URL — SsrfGuard reuse
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_url_image_downloads(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _patch_stream(monkeypatch, body=_PNG, content_type="image/png")
    loader = ImageLoader(ssrf_guard=_public_guard())
    result = await loader.load("https://example.com/pic.png?token=secret")
    assert isinstance(result, LoadedImage)
    assert result.media_type == "image/png"
    assert result.data == _PNG
    # SSRF protection: the download client MUST NOT chase redirects, so a
    # 302-to-private-IP cannot bypass the pre-flight guard. A regression that
    # re-enabled redirect-following would fail here.
    assert captured["follow_redirects"] is False


@pytest.mark.asyncio
async def test_url_loopback_refused_by_guard(workspace: Path) -> None:
    """A loopback target must be REFUSED by the shared SsrfGuard — no download."""
    guard = SsrfGuard(resolve_fn=lambda host: ["127.0.0.1"])
    loader = ImageLoader(ssrf_guard=guard)
    result = await loader.load("http://internal.local/secret.png")
    assert isinstance(result, LoadError)
    assert "egress" in result.reason.lower() or "loopback" in result.reason.lower()


@pytest.mark.asyncio
async def test_url_private_ip_literal_refused(workspace: Path) -> None:
    loader = ImageLoader(ssrf_guard=_public_guard())  # guard still classifies literals
    result = await loader.load("http://10.0.0.5/x.png")
    assert isinstance(result, LoadError)
    assert "egress" in result.reason.lower() or "private" in result.reason.lower()


@pytest.mark.asyncio
async def test_url_oversize_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    big = b"\x89PNG\r\n\x1a\n" + b"x" * (11 * 1024 * 1024)
    _patch_stream(monkeypatch, body=big, content_type="image/png")
    loader = ImageLoader(ssrf_guard=_public_guard())
    result = await loader.load("https://example.com/big.png")
    assert isinstance(result, LoadError)
    assert "too large" in result.reason.lower()


@pytest.mark.asyncio
async def test_url_non_image_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_stream(monkeypatch, body=b"<html>not an image</html>", content_type="text/html")
    loader = ImageLoader(ssrf_guard=_public_guard())
    result = await loader.load("https://example.com/page.html")
    assert isinstance(result, LoadError)
    assert "not an image" in result.reason.lower()


@pytest.mark.asyncio
async def test_url_http_error_structured(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_stream(monkeypatch, body=b"", content_type="image/png", status=404)
    loader = ImageLoader(ssrf_guard=_public_guard())
    result = await loader.load("https://example.com/missing.png")
    assert isinstance(result, LoadError)
    assert "404" in result.reason


# --------------------------------------------------------------------------- #
# Bad inputs never raise
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_empty_and_unsupported_scheme(workspace: Path) -> None:
    assert isinstance(await ImageLoader().load(""), LoadError)
    assert isinstance(await ImageLoader().load("ftp://x/y.png"), LoadError)
    assert isinstance(await ImageLoader().load("data:image/png;base64,AAAA"), LoadError)
