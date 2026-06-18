"""ImageCapability — the local-SDXL viability PROBE (E10-S4).

LOCKED operator decision: self-hosted-first, but local SDXL ONLY where a
capability probe clears. The probe runs BEFORE any multi-GB ``torch``/``diffusers``
install or weight download — so an incapable host (e.g. the Jetson/Tegra board)
NEVER pip-installs a wheel the probe would then reject. ``can_run_local=False``
means the selector treats local as not viable and considers the cloud fallback.

The gate (Fork C — probe-before-install): :meth:`LocalSdxlBackend.is_available`
calls :meth:`ImageCapability.probe` and ONLY proceeds to install/download/load
when ``can_run_local`` is True. This module performs NO install and NO download.

What the probe checks (cross-platform, never crashes on a missing file/probe):

1. **Tegra / unified-memory detection** — ``/etc/nv_tegra_release`` exists →
   integrated Orin GPU on UNIFIED system memory. ``nvidia-smi`` reports NO
   per-process VRAM on Tegra and vanilla ``pip install torch`` yields a broken/
   CPU build that OOMs unified memory. So on Tegra local SDXL is treated as NOT
   viable UNLESS a special-wheel ``torch`` is ALREADY importable with CUDA true
   (a user who pre-installed the JetPack torch wheel themselves).
2. **CUDA** — a GUARDED ``import torch`` (never crashes if torch is absent) +
   ``torch.cuda.is_available()``. No CUDA → not viable (CPU SDXL is impractical).
3. **Free system RAM** — a minimum free-RAM threshold (psutil if present, else
   ``/proc/meminfo`` on Linux, else a conservative "unknown" → not viable).
4. **Free disk** — ``shutil.disk_usage`` on ``models_dir()`` must clear the weight
   footprint BEFORE any download is attempted.

Anything the probe can't determine → ``can_run_local=False`` ("unknown → cloud").
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome

__all__ = ["ImageCapability", "ProbeResult"]

# The Tegra sentinel file present on JetPack/Jetson boards (unified memory).
_TEGRA_RELEASE = Path("/etc/nv_tegra_release")
# SDXL fp16 weights are ~7 GiB; require headroom above that before downloading.
_MIN_FREE_DISK_BYTES = 12 * 1024 * 1024 * 1024
# SDXL needs a large working set; require a conservative minimum of FREE system
# RAM (unified-memory-aware — this is system RAM, never a VRAM parse on Tegra).
_MIN_FREE_RAM_BYTES = 10 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class ProbeResult:
    """The probe's verdict: can local SDXL run, and why / why not."""

    can_run_local: bool
    reason: str

    @classmethod
    def viable(cls, reason: str) -> ProbeResult:
        return cls(can_run_local=True, reason=reason)

    @classmethod
    def not_viable(cls, reason: str) -> ProbeResult:
        return cls(can_run_local=False, reason=reason)


class ImageCapability:
    """Probes whether the host can run local SDXL. Never raises (B5)."""

    @classmethod
    def probe(cls) -> ProbeResult:
        """Run the full local-SDXL viability probe. Never raises.

        Order: Tegra → CUDA → free RAM → free disk. The FIRST failing check
        short-circuits with its structured reason. "unknown → not viable (cloud)".
        """
        log.tool.debug("[image.capability] probe: entry")
        try:
            cuda_ok, cuda_reason = cls._cuda_available()
            on_tegra = cls._is_tegra()

            # 1. Tegra/unified-memory: local SDXL is NOT viable unless a special
            #    JetPack torch wheel is already importable AND reports CUDA true.
            if on_tegra and not cuda_ok:
                return ProbeResult.not_viable(
                    "host is a Tegra/unified-memory board (/etc/nv_tegra_release "
                    "present) and no CUDA-capable torch wheel is installed; vanilla "
                    "torch is a CPU build that would OOM unified memory — local SDXL "
                    "is not viable here (use the cloud fallback)"
                )

            # 2. CUDA must be available (CPU SDXL is impractical).
            if not cuda_ok:
                return ProbeResult.not_viable(
                    f"no CUDA-capable GPU available ({cuda_reason})"
                )

            # 3. Minimum FREE system RAM (unified-memory-aware; never VRAM-parsing).
            ram_ok, ram_reason = cls._enough_free_ram()
            if not ram_ok:
                return ProbeResult.not_viable(ram_reason)

            # 4. Disk pre-check on models_dir() BEFORE any weight download.
            disk_ok, disk_reason = cls._enough_free_disk()
            if not disk_ok:
                return ProbeResult.not_viable(disk_reason)

            tegra_note = " (Tegra board with a CUDA torch wheel pre-installed)" if on_tegra else ""
            reason = f"CUDA GPU + sufficient RAM/disk{tegra_note}"
            log.tool.info(
                "[image.capability] probe: local SDXL viable",
                extra={"_fields": {"on_tegra": on_tegra}},
            )
            return ProbeResult.viable(reason)
        except Exception as exc:  # any probe error → unknown → not viable (cloud).
            log.tool.error("[image.capability] probe: unexpected failure", exc_info=exc)
            return ProbeResult.not_viable(
                f"capability probe could not determine viability "
                f"({type(exc).__name__}) — defaulting to cloud"
            )

    # ------------------------------------------------------------------- checks
    @staticmethod
    def _is_tegra() -> bool:
        """True on a Jetson/Tegra board (unified memory). Never crashes."""
        try:
            return _TEGRA_RELEASE.exists()
        except OSError:
            return False

    @staticmethod
    def _cuda_available() -> tuple[bool, str]:
        """GUARDED torch import + cuda check. Never crashes if torch is absent."""
        try:
            import torch  # noqa: PLC0415 — guarded optional heavy dep.
        except Exception as exc:  # torch absent / broken build → not CUDA-capable.
            return False, f"torch not importable: {type(exc).__name__}"
        try:
            if torch.cuda.is_available():
                return True, "torch.cuda.is_available() == True"
            return False, "torch.cuda.is_available() == False"
        except Exception as exc:  # a torch that errors on the cuda probe → no.
            return False, f"torch.cuda probe errored: {type(exc).__name__}"

    @classmethod
    def _enough_free_ram(cls) -> tuple[bool, str]:
        """Free system RAM ≥ threshold. psutil → /proc/meminfo → unknown(=no)."""
        free = cls._free_ram_bytes()
        if free is None:
            return False, (
                "free system RAM could not be determined on this platform — "
                "treating local SDXL as not viable (unknown → cloud)"
            )
        if free < _MIN_FREE_RAM_BYTES:
            return False, (
                f"insufficient free system RAM: {free // (1024 * 1024)} MiB free, "
                f"need ≥ {_MIN_FREE_RAM_BYTES // (1024 * 1024)} MiB"
            )
        return True, "sufficient free RAM"

    @staticmethod
    def _free_ram_bytes() -> int | None:
        """Best-effort free RAM in bytes. None when it can't be told (→ cloud)."""
        try:
            import psutil  # noqa: PLC0415 — optional; absent on a lean host.

            return int(psutil.virtual_memory().available)
        except Exception:  # noqa: BLE001 — psutil missing/erroring → fall through.
            pass
        # Linux fallback: parse MemAvailable from /proc/meminfo.
        meminfo = Path("/proc/meminfo")
        try:
            if meminfo.exists():
                for line in meminfo.read_text().splitlines():
                    if line.startswith("MemAvailable:"):
                        kib = int(line.split()[1])
                        return kib * 1024
        except (OSError, ValueError, IndexError):
            return None
        # Windows/Mac without psutil → can't tell cheaply → unknown.
        return None

    @classmethod
    def _enough_free_disk(cls) -> tuple[bool, str]:
        """Free disk on models_dir() ≥ the weight footprint, BEFORE any download."""
        try:
            target = StackowlHome.models_dir()
            target.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(target).free
        except OSError as exc:
            return False, (
                f"free disk on the models directory could not be determined "
                f"({type(exc).__name__}) — treating local SDXL as not viable"
            )
        if free < _MIN_FREE_DISK_BYTES:
            return False, (
                f"insufficient free disk for model weights: "
                f"{free // (1024 * 1024)} MiB free, need ≥ "
                f"{_MIN_FREE_DISK_BYTES // (1024 * 1024)} MiB"
            )
        return True, "sufficient free disk"
