"""BrowserProbe — verifies Camoufox prerequisites + auto-installs the browser binary."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("stackowl.startup")

_REQUIRED_LIBS_LINUX = ("libgtk-3", "libx11-xcb", "libasound")


@dataclass
class BrowserProbeResult:
    libs_ok: bool
    xvfb_ok: bool
    binary_ok: bool
    binary_path: Path | None
    error: str | None = None

    @property
    def ready(self) -> bool:
        """Ready to start the runtime. Only binary is required.

        Missing libs/xvfb are recoverable at runtime — Camoufox will fall back to
        ``headless=True`` (no virtual display) when xvfb is absent. The probe
        emits WARNINGs for missing libs so the operator can install them, but
        does not block startup.
        """
        return self.binary_ok


def _cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".cache"
    return root / "camoufox"


def _binary_present() -> Path | None:
    """Return the path to the Camoufox launcher binary if present, else None.

    Camoufox renames Firefox to ``camoufox`` (with a ``camoufox-bin`` companion).
    """
    root = _cache_dir()
    if not root.exists():
        return None
    for name in ("camoufox", "camoufox-bin", "firefox"):
        for candidate in root.rglob(name):
            if candidate.is_file():
                return candidate
    return None


async def _check_lib(lib_prefix: str) -> bool:
    """Best-effort check that a shared library is present on Linux via ldconfig."""
    ldconfig = shutil.which("ldconfig")
    if ldconfig is None:
        return True  # cannot verify — assume ok rather than blocking
    try:
        out = await asyncio.to_thread(lambda: os.popen(f"{ldconfig} -p").read())
    except OSError:
        return True
    return lib_prefix in out


class BrowserProbe:
    """Checks system libraries, Xvfb, and the camoufox binary; can auto-fetch the binary."""

    def __init__(self, offline: bool | None = None) -> None:
        if offline is None:
            offline = os.environ.get("STACKOWL_BROWSER_OFFLINE") == "1"
        self._offline = offline

    async def check(self, *, fetch_if_missing: bool = True) -> BrowserProbeResult:
        log.debug("[startup] browser_probe.check: entry offline=%s fetch=%s", self._offline, fetch_if_missing)
        libs_ok = await self._check_libs()
        xvfb_ok = self._check_xvfb()

        binary = _binary_present()
        binary_ok = binary is not None

        if not binary_ok and fetch_if_missing and not self._offline:
            log.info("[startup] browser_probe: camoufox binary missing — starting fetch (~622 MB)")
            ok, err = await self._fetch_binary()
            if ok:
                binary = _binary_present()
                binary_ok = binary is not None
                if binary_ok:
                    log.info("[startup] browser_probe: binary ready at %s", binary)
                else:
                    log.error("[startup] browser_probe: fetch completed but binary not found in %s", _cache_dir())
            else:
                log.error("[startup] browser_probe: binary fetch failed — %s", err)
        elif not binary_ok and self._offline:
            log.warning(
                "[startup] browser_probe: binary missing and STACKOWL_BROWSER_OFFLINE=1 — "
                "browser tools will be unavailable"
            )

        result = BrowserProbeResult(
            libs_ok=libs_ok,
            xvfb_ok=xvfb_ok,
            binary_ok=binary_ok,
            binary_path=binary,
            error=None if (libs_ok and xvfb_ok and binary_ok) else self._summarize_missing(libs_ok, xvfb_ok, binary_ok),
        )
        log.info(
            "[startup] browser_probe.check: exit libs=%s xvfb=%s binary=%s ready=%s",
            libs_ok, xvfb_ok, binary_ok, result.ready,
        )
        return result

    async def _check_libs(self) -> bool:
        if not sys.platform.startswith("linux"):
            return True
        missing = [lib for lib in _REQUIRED_LIBS_LINUX if not await _check_lib(lib)]
        if missing:
            log.warning(
                "[startup] browser_probe: missing system libraries %s — install with "
                "'sudo apt install -y libgtk-3-0 libx11-xcb1 libasound2'",
                missing,
            )
            return False
        return True

    def _check_xvfb(self) -> bool:
        if not sys.platform.startswith("linux"):
            return True
        if shutil.which("Xvfb") is None:
            log.warning(
                "[startup] browser_probe: Xvfb not found — install with 'sudo apt install -y xvfb' "
                "for headless='virtual' (stealthier than headless=True)"
            )
            return False
        return True

    async def _fetch_binary(self) -> tuple[bool, str | None]:
        cmd = [sys.executable, "-m", "camoufox", "fetch"]
        log.info("[startup] browser_probe: running %s", " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            return False, f"could not launch fetch subprocess: {exc}"

        assert proc.stdout is not None
        last_line = ""
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            last_line = line
            log.info("[startup] browser_probe: fetch: %s", line)

        rc = await proc.wait()
        if rc != 0:
            return False, f"fetch exited with code {rc} ({last_line!r})"
        return True, None

    def _summarize_missing(self, libs_ok: bool, xvfb_ok: bool, binary_ok: bool) -> str:
        parts = []
        if not libs_ok:
            parts.append("missing system libs (libgtk-3-0 / libx11-xcb1 / libasound2)")
        if not xvfb_ok:
            parts.append("Xvfb not installed")
        if not binary_ok:
            parts.append("camoufox firefox binary not fetched")
        return "; ".join(parts)
