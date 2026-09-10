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

#: DEBIAN PACKAGE-name prefixes, deliberately — they are what the remediation
#: message tells the operator to install. They are matched CASE-INSENSITIVELY
#: against ldconfig's SONAME output, which uses different capitalisation
#: (``libX11-xcb.so.1``); see :func:`_check_lib`.
_REQUIRED_LIBS_LINUX = ("libgtk-3", "libx11-xcb", "libasound")

#: WHAT AN OPERATOR SHOULD DO when the browser capability is absent, one string
#: per CAUSE. They live here because this module is what decides the causes — the
#: probe runs the checks and owns the auto-install — and because two other places
#: need the same words: `startup/orchestrator.py` registers the capability as
#: unavailable, and `health/contributors.py` reports the same subsystem to
#: `stackowl health`. Before this, the health contributor carried its own copy of
#: the install advice and the capability registry carried NONE at all.
#:
#: MEASURED 2026-09-10, and it is why these exist: of 57 `[capabilities] resolve:
#: capability UNAVAILABLE` records in the whole retained corpus, **57 carry
#: `remedy: null`**. `Availability.remedy` exists, `resolve()` reads it,
#: `tool_search` renders it as "— fix: …", and `_UnavailableCapability` accepts it
#: as a constructor argument — five layers, complete end to end, and the single
#: call site never passed one. So the "— fix:" branch has never rendered, ever.
REMEDY_NOT_HOSTED_HERE = (
    "nothing to do — the browser is hosted by the CORE process and browser tools "
    "work there; this process is the gateway, which never hosts it"
)
#: Both strings named here are LIVE and greppable, which invariant 4 of D14.4
#: requires of any remedy: MEASURED 2026-09-10 across the retained corpus,
#: `browser_probe.check` appears 1,108 times (once a boot) and `browser_install`
#: 2,216. Advice that names a log line nobody emits is worse than none.
REMEDY_BINARY_MISSING = (
    "the browser binary auto-installs at startup — read `[startup] "
    "browser_install` and `[startup] browser_probe.check` in the log, plus the "
    "reason reported above"
)
REMEDY_PROBE_DID_NOT_RUN = (
    "the startup probe never ran, so nothing is known about the binary — restart "
    "with `./start.sh` and read `[startup] browser_probe.check` in the log"
)
REMEDY_CAUSE_UNKNOWN = (
    "the guard rejected the runtime but no known cause matched — this is a defect "
    "in the startup guard itself; read `[startup] gateway: browser runtime skipped`"
)


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
    """Best-effort check that a shared library is present on Linux via ldconfig.

    CASE-INSENSITIVE, AND THAT IS THE WHOLE POINT. MEASURED 2026-09-01: this had
    reported ``missing system libraries ['libx11-xcb']`` on EVERY boot — 644
    times in the retained logs — and the library was installed the entire time.
    ``ldconfig -p`` spells it ``libX11-xcb.so.1`` with a capital X; the names in
    :data:`_REQUIRED_LIBS_LINUX` are DEBIAN PACKAGE names (``libx11-xcb1``),
    which are lowercase by policy. Two different vocabularies compared with one
    case-sensitive substring test.

    ``libgtk-3`` and ``libasound`` passed only because their SONAME happens to be
    lowercase too — by luck, not design, which is why this is fixed as a class
    rather than by respelling one entry.

    WHAT IT COST: nothing at runtime — ``ready`` requires only the binary and the
    libs are advisory, so the browser was never blocked (163 browser_navigate
    turns prove it). What it cost was TRUST: a boot-time instruction to run
    ``sudo apt install`` for a package already present, 644 times, in logs the
    operator reads. A warning that is always wrong teaches everyone to ignore
    warnings — it cost this session real time as a suspected cause of browser
    timeouts before being measured and refuted.
    """
    ldconfig = shutil.which("ldconfig")
    if ldconfig is None:
        return True  # cannot verify — assume ok rather than blocking
    try:
        out = await asyncio.to_thread(lambda: os.popen(f"{ldconfig} -p").read())
    except OSError:
        return True
    return lib_prefix.casefold() in out.casefold()


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
        # INFO, and new. The absence of a warning is not evidence the check RAN —
        # that ambiguity is what let a permanently-false result sit unnoticed for
        # 644 boots. Saying so positively makes the passing case observable.
        log.info(
            "[startup] browser_probe: all required system libraries present (%s)",
            ", ".join(_REQUIRED_LIBS_LINUX),
        )
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
