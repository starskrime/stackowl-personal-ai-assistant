"""Launch a CDP browser the platform OWNS, so no human has to.

Bakir, 2026-08-26: "you need to fix platform to work with tool not manuall fix
issue for the platform. Because when we ship it to customer device i will not
have access to help."

THAT IS THE WHOLE POINT OF THIS MODULE. CDP support shipped earlier the same day
and it worked — but only because a person had already started a browser by hand
and written its URL into config. On a customer device nobody does either. A
capability that needs an operator at a terminal is not a capability; it is a
demo.

WHAT THIS DOES. Finds a Chromium-family binary that is already on the machine,
starts it headless on a free port with its own throwaway profile, and hands back
a CDP URL. The platform then attaches to it exactly as it would attach to a
browser someone else started.

WHAT IT DELIBERATELY DOES NOT DO. It does not download or install a browser. That
is a separate decision with bandwidth and disk consequences on someone else's
device, and it belongs to the install path rather than to a failing tool call at
runtime. If no binary is present, this fails with a message naming what it looked
for — which is a far better customer-device outcome than silently doing nothing.

OWNERSHIP IS THE CRITICAL DISTINCTION, and it is why this is not merged into the
attach backend. A browser WE launched is ours to stop; a browser the operator was
already running must survive us. Conflating the two either leaks a process on
every restart or kills someone's real browser. The runtime tracks which case it
is in and this module only ever produces the first.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome

#: Chromium-family binaries, in preference order. Names only — resolved through
#: PATH, never hardcoded absolute paths, because this has to work on a device
#: whose layout we have never seen.
_CANDIDATES: tuple[str, ...] = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "brave-browser",
    "microsoft-edge",
)

#: Flags that make a browser safe to drive unattended. `--headless=new` is the
#: modern headless mode; the rest suppress first-run UI and GPU paths that do not
#: exist on a headless box.
_FLAGS: tuple[str, ...] = (
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--disable-dev-shm-usage",
)

#: How long to wait for the port to accept a connection before giving up.
_READY_TIMEOUT_SEC = 20.0


@dataclass(frozen=True)
class LaunchedBrowser:
    """A browser this process started and is responsible for stopping."""

    cdp_url: str
    port: int
    binary: str
    process: subprocess.Popen[bytes]


def find_browser_binary() -> str | None:
    """First Chromium-family binary on PATH, or None.

    Returns the name rather than raising, so the caller can decide whether an
    absent browser is fatal — on most devices it is simply a capability the
    platform does not have, and it should say so rather than crash.
    """
    for name in _CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _free_port() -> int:
    """A port the OS says is free right now.

    Bound and released rather than picked from a range: a hardcoded 9222 collides
    with the operator's own browser, which is the one process we must never
    disturb.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _wait_until_ready(port: int, timeout: float = _READY_TIMEOUT_SEC) -> bool:
    """True once the CDP port accepts a connection."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return True
        except OSError:
            await asyncio.sleep(0.25)
    return False


async def launch_cdp_browser() -> LaunchedBrowser | None:
    """Start a headless Chromium-family browser on a free CDP port.

    Returns None when no binary exists — the honest outcome on a device without
    one, and the caller degrades rather than crashing.
    """
    # 1. ENTRY
    log.engine.debug("[browser] cdp_launcher.launch: entry")
    binary = find_browser_binary()
    if binary is None:
        # 2. DECISION — nothing to launch. Name what was looked for, because the
        # operator of a customer device cannot read our source to find out.
        log.engine.warning(
            "[browser] cdp_launcher: no Chromium-family browser on PATH — "
            "cannot self-provision a CDP browser",
            extra={"_fields": {"looked_for": list(_CANDIDATES)}},
        )
        return None

    port = _free_port()
    profile = StackowlHome.browser_profiles_dir() / f"cdp-{port}"
    profile.mkdir(parents=True, exist_ok=True)
    argv = [
        binary,
        *_FLAGS,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile}",
    ]
    try:
        # 3. STEP — start it detached from our stdio so a chatty browser can
        # never fill a pipe nobody drains and wedge itself.
        proc = subprocess.Popen(  # noqa: S603 — argv is built from a PATH lookup
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:  # B5 — every except logs
        log.engine.error(
            "[browser] cdp_launcher: launch failed",
            exc_info=exc,
            extra={"_fields": {"binary": binary, "port": port}},
        )
        return None

    if not await _wait_until_ready(port):
        log.engine.error(
            "[browser] cdp_launcher: browser started but the CDP port never "
            "accepted a connection — stopping it rather than leaking it",
            extra={"_fields": {"binary": binary, "port": port,
                               "timeout_sec": _READY_TIMEOUT_SEC}},
        )
        stop_cdp_browser(
            LaunchedBrowser(cdp_url="", port=port, binary=binary, process=proc)
        )
        return None

    url = f"http://127.0.0.1:{port}"
    # 4. EXIT — INFO, because this is the evidence that the platform provisioned
    # its own browser without anyone helping it.
    log.engine.info(
        "[browser] cdp_launcher: launched a CDP browser (platform-owned)",
        extra={"_fields": {"binary": binary, "cdp_url": url, "pid": proc.pid}},
    )
    return LaunchedBrowser(cdp_url=url, port=port, binary=binary, process=proc)


def stop_cdp_browser(launched: LaunchedBrowser) -> None:
    """Stop a browser THIS PROCESS started. Never call this on an attached one."""
    log.engine.debug(
        "[browser] cdp_launcher.stop: entry",
        extra={"_fields": {"pid": launched.process.pid}},
    )
    proc = launched.process
    if proc.poll() is not None:
        return
    with contextlib.suppress(Exception):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    log.engine.info(
        "[browser] cdp_launcher: stopped the platform-owned browser",
        extra={"_fields": {"pid": proc.pid, "port": launched.port}},
    )


def cleanup_profile(launched: LaunchedBrowser) -> None:
    """Remove the throwaway profile. Best-effort; a stale dir is not a failure."""
    profile = StackowlHome.browser_profiles_dir() / f"cdp-{launched.port}"
    with contextlib.suppress(Exception):
        shutil.rmtree(profile, ignore_errors=True)


def profile_dir_for(port: int) -> Path:
    return StackowlHome.browser_profiles_dir() / f"cdp-{port}"
