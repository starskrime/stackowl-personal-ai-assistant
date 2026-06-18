"""E10-S4 — image_generate tool: select/generate + PATH-not-bytes + egress+cost.

Network-free / install-free: the tool is driven with an INJECTED selector backed
by fake backends — no probe, no pip install, no diffusion library, no HTTP.
Asserts:

* a local backend → a PATH under media_dir() surfaced (not bytes), size/backend
  reported, the file exists, NO egress note;
* a cloud backend → the egress + COST disclosure PREPENDED (prompt left the box);
* selector local-first (a local backend is preferred over a configured cloud);
* nothing available → an ACTIONABLE structured unavailable (success False, never raises);
* under an ACTIVE TestModeGuard the real selector reports unavailable with NO
  install/network/generate (load-bearing);
* the full prompt is NEVER logged (only its length).
"""

from __future__ import annotations

import logging
import subprocess as _subprocess

import pytest

from stackowl.config.settings import ImageSettings
from stackowl.config.test_mode import TestModeGuard
from stackowl.media.image.base import ImageAvailability, ImageBackend, ImageResult
from stackowl.media.image.selector import ImageSelector
from stackowl.paths import StackowlHome
from stackowl.tools.media.image_generate import ImageGenerateTool

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):  # noqa: ANN202
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


class _FakeBackend(ImageBackend):
    """A backend that writes a real (tiny) file under media_dir and reports locality."""

    def __init__(self, name: str, *, local: bool, available: bool = True, reason: str | None = None) -> None:
        self._name = name
        self._local = local
        self._available = available
        self._reason = reason
        self.gen_called_with_prompt: str | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return self._local

    async def is_available(self) -> ImageAvailability:
        return ImageAvailability.ok() if self._available else ImageAvailability.no(self._reason or "down")

    async def generate(self, prompt: str, *, size: str | None = None) -> ImageResult | str:
        self.gen_called_with_prompt = prompt
        out_dir = StackowlHome.media_dir() / "image"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self._name}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return ImageResult(path=str(path), size=size or "1024x1024", backend=self._name, is_local=self._local)


def _selector(local: ImageBackend, cloud: ImageBackend, *, engine: str = "auto") -> ImageSelector:
    return ImageSelector(ImageSettings(engine=engine), local=local, cloud=cloud)  # type: ignore[arg-type]


async def test_local_returns_path_under_media_dir_no_egress() -> None:
    local = _FakeBackend("local-sdxl", local=True)
    cloud = _FakeBackend("cloud", local=False)
    tool = ImageGenerateTool(selector=_selector(local, cloud))
    res = await tool.execute(prompt="a blue owl", size="512x512")
    assert res.success is True
    assert local.gen_called_with_prompt == "a blue owl"
    assert cloud.gen_called_with_prompt is None
    assert StackowlHome.media_dir().as_posix() in res.output
    assert "local-sdxl.png" in res.output
    assert "backend=local-sdxl" in res.output and "size=512x512" in res.output
    out_path = StackowlHome.media_dir() / "image" / "local-sdxl.png"
    assert out_path.exists()
    assert "[Cloud image generation:" not in res.output


async def test_cloud_backend_prepends_egress_and_cost_disclosure() -> None:
    local = _FakeBackend("local-sdxl", local=True, available=False, reason="probe: Tegra")
    cloud = _FakeBackend("cloud", local=False)
    tool = ImageGenerateTool(selector=_selector(local, cloud, engine="auto"))
    res = await tool.execute(prompt="secret scene")
    assert res.success is True
    assert cloud.gen_called_with_prompt == "secret scene"
    assert res.output.startswith("[Cloud image generation:")
    assert "'cloud'" in res.output  # names the backend
    assert "left this machine" in res.output
    assert "cost" in res.output.lower()  # cost disclosure
    assert "backend=cloud" in res.output


async def test_local_preferred_over_configured_cloud() -> None:
    local = _FakeBackend("local-sdxl", local=True, available=True)
    cloud = _FakeBackend("cloud", local=False, available=True)
    tool = ImageGenerateTool(selector=_selector(local, cloud, engine="auto"))
    res = await tool.execute(prompt="hi")
    assert res.success is True
    assert local.gen_called_with_prompt == "hi"
    assert cloud.gen_called_with_prompt is None
    assert "[Cloud image generation:" not in res.output


async def test_no_backend_is_actionable_never_raises() -> None:
    local = _FakeBackend("local-sdxl", local=True, available=False, reason="probe: no CUDA")
    cloud = _FakeBackend("cloud", local=False, available=False, reason="no key")
    tool = ImageGenerateTool(selector=_selector(local, cloud, engine="auto"))
    res = await tool.execute(prompt="hello")  # must NOT raise
    assert res.success is False
    reason = (res.error or "").lower()
    assert "image generation unavailable" in reason
    assert "cloud_enabled" in reason  # actionable


async def test_empty_prompt_is_structured_error() -> None:
    local = _FakeBackend("local-sdxl", local=True)
    cloud = _FakeBackend("cloud", local=False)
    tool = ImageGenerateTool(selector=_selector(local, cloud))
    res = await tool.execute(prompt="   ")
    assert res.success is False
    assert "prompt" in (res.error or "").lower()


async def test_generation_error_degrades() -> None:
    class _ErrBackend(_FakeBackend):
        async def generate(self, prompt: str, *, size: str | None = None) -> ImageResult | str:
            return "local image generation failed: Boom"

    local = _ErrBackend("local-sdxl", local=True)
    cloud = _FakeBackend("cloud", local=False)
    tool = ImageGenerateTool(selector=_selector(local, cloud))
    res = await tool.execute(prompt="hello")
    assert res.success is False
    assert "generation failed" in (res.error or "")


async def test_test_mode_real_selector_unavailable_no_install_no_network() -> None:
    """LOAD-BEARING: with the REAL (default) selector under an ACTIVE TestModeGuard,
    the tool must return a structured unavailable WITHOUT installing or hitting the
    network. (The probe gates the local install before test mode is even reached on
    an incapable host; on a capable host TestModeGuard stops the install.)"""
    import urllib.request as _urllib

    subprocess_calls: list[object] = []
    urlopen_calls: list[object] = []

    tool = ImageGenerateTool()  # real selector, built from Settings().image
    TestModeGuard.activate()
    try:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(_subprocess, "run", lambda *a, **k: subprocess_calls.append((a, k)))
            mp.setattr(_urllib, "urlopen", lambda *a, **k: urlopen_calls.append((a, k)))
            res = await tool.execute(prompt="anything")  # must NOT raise
    finally:
        TestModeGuard.deactivate()

    assert res.success is False  # no backend available under test mode
    assert subprocess_calls == []  # no pip install
    assert urlopen_calls == []  # no network egress


async def test_full_prompt_never_logged(caplog) -> None:
    secret = "this is a very secret image prompt that must never appear in logs"
    local = _FakeBackend("local-sdxl", local=True)
    cloud = _FakeBackend("cloud", local=False)
    tool = ImageGenerateTool(selector=_selector(local, cloud))
    with caplog.at_level(logging.DEBUG):
        res = await tool.execute(prompt=secret)
    assert res.success is True
    assert secret not in caplog.text


async def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry.with_defaults()
    tool = reg.get("image_generate")
    assert tool is not None
    assert tool.manifest.action_severity == "read"
    assert tool.manifest.toolset_group == "media"
