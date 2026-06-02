"""E10-S4 — LocalSdxl + Cloud backend contracts (network-free, install-free).

NEVER installs torch/diffusers or hits a network. The probe is PATCHED to simulate
each host shape; the pipeline loader is PATCHED to a fake. Critically:

* probe says NO → the local backend is unavailable AND NEVER attempts the multi-GB
  install (subprocess.run is spied and asserted not-called);
* probe says YES + a fake pipeline → generate returns a PATH under media_dir (not bytes);
* under an ACTIVE TestModeGuard the REAL loader reports unavailable WITHOUT
  shelling out to pip OR opening a network connection (load-bearing);
* the cloud backend is disabled-by-default + opt-in, asserted with no HTTP.
"""

from __future__ import annotations

import subprocess as _subprocess
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.media.image.base import ImageResult
from stackowl.media.image.capability import ProbeResult
from stackowl.media.image.cloud import CloudImageBackend
from stackowl.media.image.local_sdxl import LocalSdxlBackend
from stackowl.paths import StackowlHome

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


class _FakeImage:
    def save(self, path: str) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")


class _FakeResult:
    images = [_FakeImage()]


class _FakePipeline:
    """Stands in for the loaded diffusion pipeline."""

    def to(self, device: str) -> _FakePipeline:
        return self

    def __call__(self, *, prompt: str, width: int, height: int) -> _FakeResult:
        return _FakeResult()


class _FakePipelineCls:
    @staticmethod
    def from_pretrained(model: str, **kwargs: object) -> _FakePipeline:
        return _FakePipeline()


class _FakeTorch:
    float16 = "float16"


def _viable() -> ProbeResult:
    return ProbeResult.viable("CUDA GPU + sufficient RAM/disk")


def _not_viable() -> ProbeResult:
    return ProbeResult.not_viable("host is a Tegra/unified-memory board")


# ----------------------------------------------------------------- LocalSdxl
async def test_local_unavailable_when_probe_says_no_never_installs(monkeypatch) -> None:
    """The GATE: probe NO → unavailable AND the multi-GB install is NEVER attempted."""
    monkeypatch.setattr(
        "stackowl.media.image.local_sdxl.ImageCapability.probe", staticmethod(_not_viable)
    )
    spy_calls: list[object] = []
    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: spy_calls.append((a, k)))

    backend = LocalSdxlBackend()
    avail = await backend.is_available()
    assert avail.available is False
    assert "not available" in (avail.reason or "")
    assert "Tegra" in (avail.reason or "")
    # The pip install NEVER fired — the probe gated it BEFORE any install.
    assert spy_calls == []


async def test_local_success_returns_path_not_bytes(monkeypatch) -> None:
    monkeypatch.setattr(
        "stackowl.media.image.local_sdxl.ImageCapability.probe", staticmethod(_viable)
    )
    backend = LocalSdxlBackend()
    # Patch the lazy loader: skip pip + model download, return a fake pipeline.
    monkeypatch.setattr(
        backend, "_import_pipeline", lambda: (_FakePipelineCls, _FakeTorch)
    )

    out = await backend.generate("a red owl", size="512x512")
    assert isinstance(out, ImageResult)  # a structured result, not bytes
    assert out.is_local is True
    assert out.backend == "local-sdxl"
    assert out.size == "512x512"
    p = Path(out.path)
    assert p.exists()
    assert StackowlHome.media_dir() in p.parents


async def test_local_install_failure_is_unavailable_never_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        "stackowl.media.image.local_sdxl.ImageCapability.probe", staticmethod(_viable)
    )
    backend = LocalSdxlBackend()

    def _boom() -> object:
        raise RuntimeError("pip wheel build failed")

    monkeypatch.setattr(backend, "_import_pipeline", _boom)
    avail = await backend.is_available()
    assert avail.available is False
    assert "could not initialize" in (avail.reason or "")
    out = await backend.generate("x", size=None)
    assert isinstance(out, str)
    assert "could not initialize" in out


async def test_local_test_mode_unavailable_no_shell_out_no_network(monkeypatch) -> None:
    """LOAD-BEARING: under an ACTIVE TestModeGuard + a viable probe, the REAL
    (un-mocked) loader must report unavailable WITHOUT shelling out to pip OR
    opening a network connection."""
    import urllib.request as _urllib

    monkeypatch.setattr(
        "stackowl.media.image.local_sdxl.ImageCapability.probe", staticmethod(_viable)
    )
    subprocess_calls: list[object] = []
    urlopen_calls: list[object] = []
    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: subprocess_calls.append((a, k)))
    monkeypatch.setattr(_urllib, "urlopen", lambda *a, **k: urlopen_calls.append((a, k)))

    backend = LocalSdxlBackend()
    TestModeGuard.activate()
    try:
        avail = await backend.is_available()
    finally:
        TestModeGuard.deactivate()

    assert avail.available is False
    assert "could not initialize" in (avail.reason or "")
    assert subprocess_calls == []  # the pip install never fired
    assert urlopen_calls == []  # no network egress


async def test_local_test_mode_blocks_load_even_when_deps_present(monkeypatch) -> None:
    """FF fix: the guard is hoisted ABOVE the dep import, so even on a host where
    torch/diffusers are already importable, NO from_pretrained (network) / generate
    runs under test mode — _import_pipeline is never even reached."""
    monkeypatch.setattr(
        "stackowl.media.image.local_sdxl.ImageCapability.probe", staticmethod(_viable)
    )
    import_calls: list[int] = []
    backend = LocalSdxlBackend()
    # Deps "present": _import_pipeline WOULD succeed — but the guard must fire first.
    monkeypatch.setattr(
        backend,
        "_import_pipeline",
        lambda: (import_calls.append(1), (_FakePipelineCls, _FakeTorch))[1],
    )
    TestModeGuard.activate()
    try:
        avail = await backend.is_available()
    finally:
        TestModeGuard.deactivate()

    assert avail.available is False
    assert import_calls == []  # the guard gated BEFORE the dep import / from_pretrained


async def test_local_invalid_size_degrades(monkeypatch) -> None:
    monkeypatch.setattr(
        "stackowl.media.image.local_sdxl.ImageCapability.probe", staticmethod(_viable)
    )
    backend = LocalSdxlBackend()
    monkeypatch.setattr(backend, "_import_pipeline", lambda: (_FakePipelineCls, _FakeTorch))
    out = await backend.generate("x", size="not-a-size")
    assert isinstance(out, str)
    assert "invalid size" in out


# --------------------------------------------------------------------- Cloud
async def test_cloud_disabled_by_default() -> None:
    backend = CloudImageBackend(base_url="https://x/v1", api_key_ref="")
    avail = await backend.is_available()
    assert avail.available is False
    assert "disabled" in (avail.reason or "")
    out = await backend.generate("hi", size=None)
    assert isinstance(out, str)
    assert "disabled" in out


async def test_cloud_available_when_key_resolves(monkeypatch) -> None:
    monkeypatch.setenv("FAKE_IMG_KEY", "sk-fake")
    backend = CloudImageBackend(base_url="https://x/v1", api_key_ref="FAKE_IMG_KEY")
    avail = await backend.is_available()
    assert avail.available is True


async def test_cloud_unresolvable_key_is_unavailable() -> None:
    backend = CloudImageBackend(base_url="https://x/v1", api_key_ref="MISSING_IMG_KEY_VAR")
    avail = await backend.is_available()
    assert avail.available is False
    assert "did not resolve" in (avail.reason or "")
