"""E10-S4 — ImageCapability probe: Tegra/CUDA/RAM/disk → can_run_local + "unknown→cloud".

Network-free / install-free: the probe NEVER installs torch — it only does a
GUARDED import. We patch the probe's internal checks to simulate each host shape
and assert the verdict + that it short-circuits in the right order and NEVER raises.
"""

from __future__ import annotations

from stackowl.media.image import capability as cap
from stackowl.media.image.capability import ImageCapability


def _patch_checks(
    monkeypatch,  # noqa: ANN001
    *,
    tegra: bool,
    cuda: tuple[bool, str],
    ram: tuple[bool, str] = (True, "ok"),
    disk: tuple[bool, str] = (True, "ok"),
) -> None:
    monkeypatch.setattr(ImageCapability, "_is_tegra", staticmethod(lambda: tegra))
    monkeypatch.setattr(ImageCapability, "_cuda_available", staticmethod(lambda: cuda))
    monkeypatch.setattr(ImageCapability, "_enough_free_ram", classmethod(lambda cls: ram))
    monkeypatch.setattr(ImageCapability, "_enough_free_disk", classmethod(lambda cls: disk))


def test_tegra_without_cuda_wheel_is_not_viable(monkeypatch) -> None:
    # Simulated Jetson/Tegra board, vanilla (CPU) torch → local SDXL NOT viable.
    _patch_checks(monkeypatch, tegra=True, cuda=(False, "torch.cuda False"))
    res = ImageCapability.probe()
    assert res.can_run_local is False
    assert "Tegra" in res.reason or "unified-memory" in res.reason


def test_x86_cuda_ram_disk_ok_is_viable(monkeypatch) -> None:
    _patch_checks(monkeypatch, tegra=False, cuda=(True, "cuda True"))
    res = ImageCapability.probe()
    assert res.can_run_local is True
    assert "CUDA" in res.reason


def test_no_cuda_is_not_viable(monkeypatch) -> None:
    _patch_checks(monkeypatch, tegra=False, cuda=(False, "torch not importable: ImportError"))
    res = ImageCapability.probe()
    assert res.can_run_local is False
    assert "no CUDA" in res.reason


def test_insufficient_ram_is_not_viable(monkeypatch) -> None:
    _patch_checks(
        monkeypatch, tegra=False, cuda=(True, "cuda True"),
        ram=(False, "insufficient free system RAM: 512 MiB free"),
    )
    res = ImageCapability.probe()
    assert res.can_run_local is False
    assert "RAM" in res.reason


def test_insufficient_disk_is_not_viable(monkeypatch) -> None:
    _patch_checks(
        monkeypatch, tegra=False, cuda=(True, "cuda True"),
        disk=(False, "insufficient free disk for model weights: 100 MiB free"),
    )
    res = ImageCapability.probe()
    assert res.can_run_local is False
    assert "disk" in res.reason


def test_tegra_with_cuda_wheel_is_viable(monkeypatch) -> None:
    # A Tegra user who pre-installed a CUDA-capable JetPack torch wheel → viable.
    _patch_checks(monkeypatch, tegra=True, cuda=(True, "cuda True"))
    res = ImageCapability.probe()
    assert res.can_run_local is True
    assert "Tegra" in res.reason  # notes the special-wheel case


def test_unknown_ram_defaults_to_not_viable_cloud(monkeypatch) -> None:
    # "unknown → cloud": when free RAM can't be determined, local is not viable.
    monkeypatch.setattr(ImageCapability, "_is_tegra", staticmethod(lambda: False))
    monkeypatch.setattr(ImageCapability, "_cuda_available", staticmethod(lambda: (True, "cuda True")))
    monkeypatch.setattr(ImageCapability, "_free_ram_bytes", staticmethod(lambda: None))
    res = ImageCapability.probe()
    assert res.can_run_local is False
    assert "could not be determined" in res.reason


def test_probe_never_raises_on_internal_error(monkeypatch) -> None:
    def _boom() -> bool:
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(ImageCapability, "_is_tegra", staticmethod(_boom))
    res = ImageCapability.probe()  # must NOT raise
    assert res.can_run_local is False
    assert "could not determine" in res.reason


def test_guarded_torch_import_does_not_crash_when_absent() -> None:
    # The REAL _cuda_available with torch absent on CI must return (False, ...),
    # never raise — proving the guarded import is safe.
    ok, reason = ImageCapability._cuda_available()
    assert ok is False
    assert "torch" in reason.lower()


def test_tegra_sentinel_path_is_the_nv_release_file() -> None:
    assert cap._TEGRA_RELEASE.name == "nv_tegra_release"
