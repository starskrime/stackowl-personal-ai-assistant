"""E10-S2 — vision_analyze tool: load/select/complete + egress disclosure.

Unit coverage of the tool's five contract points (all driven through the REAL
ImageLoader + REAL VisionSelector + a real ProviderRegistry; only the vision
provider's ``complete()`` output is canned):

* a load error (bad path) → structured failure, NO backend hit;
* no provider registry / no vision provider → ACTIONABLE structured failure;
* a CLOUD vision backend → egress header PREPENDED to the description;
* a LOCAL vision backend → the description with NO egress header;
* a provider that raises inside complete() → structured failure, NEVER raises.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.media.vision_analyze import VisionAnalyzeTool

pytestmark = pytest.mark.asyncio


# --- a minimal VALID 1x1 PNG written with stdlib (no Pillow) --------------------
def _png_bytes() -> bytes:
    """A genuine 1x1 opaque-red PNG (magic-byte sniffable as image/png)."""

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit, RGB
    raw = b"\x00\xff\x00\x00"  # one filtered scanline: red pixel
    idat = zlib.compress(raw)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _write_png(home: Path) -> str:
    """Write the PNG under the workspace data root so the loader genuinely loads it."""
    from stackowl.tools.io.path_guard import data_root

    target = data_root() / "vision_fixture.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_png_bytes())
    return str(target)


# --- the ONLY mock: a vision-capable provider with canned/raising complete() ----
class _VisionMock(MockProvider):
    """A MockProvider that reports vision capability and a canned description.

    ``raises=True`` makes complete() raise to prove the tool degrades (never raises).
    """

    def __init__(self, name: str, *, description: str = "a tiny red square", raises: bool = False) -> None:
        super().__init__(name=name)
        self._description = description
        self._raises = raises
        self.seen_image_bytes = False

    @property
    def supports_vision(self) -> bool:  # type: ignore[override]
        return True

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        if self._raises:
            raise RuntimeError("vision backend exploded")
        # Prove the image rode along as an image-MIME DocumentBlock.
        for m in messages:
            for d in m.documents:
                if d.media_type.startswith("image/"):
                    self.seen_image_bytes = True
        return CompletionResult(
            content=self._description, input_tokens=1, output_tokens=1,
            model="vision-mock", provider_name=self.name, duration_ms=1.0,
        )


def _registry_with(
    *, locality: Literal["local", "cloud"], raises: bool = False, description: str = "a tiny red square"
) -> tuple[ProviderRegistry, _VisionMock]:
    reg = ProviderRegistry()
    mock = _VisionMock("vision", description=description, raises=raises)
    base_url = "http://localhost:11434/v1" if locality == "local" else "https://api.cloud.example/v1"
    reg.register_mock("vision", mock, tier="fast", base_url=base_url)
    return reg, mock


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):  # noqa: ANN202
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _services(registry: ProviderRegistry | None):  # noqa: ANN202
    token = set_services(StepServices(provider_registry=registry))
    return token


async def test_load_error_is_structured_no_backend_hit(tmp_path) -> None:
    reg, mock = _registry_with(locality="local")
    token = _services(reg)
    try:
        res = await VisionAnalyzeTool().execute(image="does_not_exist.png", question="what is this?")
    finally:
        reset_services(token)
    assert res.success is False
    assert "could not load image" in (res.error or "")
    # The backend was NEVER reached — no image bytes observed.
    assert mock.seen_image_bytes is False


async def test_no_provider_registry_is_actionable(tmp_path) -> None:
    img = _write_png(tmp_path)
    token = _services(None)  # nothing wired → unavailable
    try:
        res = await VisionAnalyzeTool().execute(image=img)
    finally:
        reset_services(token)
    assert res.success is False
    assert "unavailable" in (res.error or "").lower()


async def test_no_vision_provider_is_actionable(tmp_path) -> None:
    img = _write_png(tmp_path)
    # A registry with only a TEXT provider (supports_vision False) → actionable.
    reg = ProviderRegistry()
    reg.register_mock("text", MockProvider("text"), tier="fast", base_url="http://localhost:1/v1")
    token = _services(reg)
    try:
        res = await VisionAnalyzeTool().execute(image=img)
    finally:
        reset_services(token)
    assert res.success is False
    err = (res.error or "").lower()
    assert "vision" in err and ("install" in err or "configure" in err)


async def test_cloud_backend_prepends_egress_header(tmp_path) -> None:
    img = _write_png(tmp_path)
    reg, mock = _registry_with(locality="cloud", description="a tiny red square")
    token = _services(reg)
    try:
        res = await VisionAnalyzeTool().execute(image=img, question="what is in this image?")
    finally:
        reset_services(token)
    assert res.success is True
    assert mock.seen_image_bytes is True  # the image really rode along
    assert res.output.startswith("[Cloud vision:")
    assert "'vision'" in res.output  # names the provider
    assert "left this machine" in res.output
    assert "a tiny red square" in res.output  # the real description is included


async def test_local_backend_has_no_egress_header(tmp_path) -> None:
    img = _write_png(tmp_path)
    reg, mock = _registry_with(locality="local", description="a tiny red square")
    token = _services(reg)
    try:
        res = await VisionAnalyzeTool().execute(image=img, question="describe it")
    finally:
        reset_services(token)
    assert res.success is True
    assert mock.seen_image_bytes is True
    assert "[Cloud vision:" not in res.output  # stayed on-box → NO egress note
    assert res.output == "a tiny red square"  # exactly the description, unprefixed


async def test_provider_raise_degrades_never_raises(tmp_path) -> None:
    img = _write_png(tmp_path)
    reg, _mock = _registry_with(locality="local", raises=True)
    token = _services(reg)
    try:
        res = await VisionAnalyzeTool().execute(image=img)  # must NOT raise
    finally:
        reset_services(token)
    assert res.success is False
    assert "failed" in (res.error or "").lower()


async def test_default_question_used_when_omitted(tmp_path) -> None:
    img = _write_png(tmp_path)
    reg, mock = _registry_with(locality="local")
    token = _services(reg)
    try:
        res = await VisionAnalyzeTool().execute(image=img)
    finally:
        reset_services(token)
    assert res.success is True
    assert mock.seen_image_bytes is True


async def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry.with_defaults()
    tool = reg.get("vision_analyze")
    assert tool is not None
    assert tool.manifest.action_severity == "read"
    assert tool.manifest.toolset_group == "media"
