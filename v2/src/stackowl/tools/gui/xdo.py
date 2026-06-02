"""``XdoAdapter`` — the Linux/X11 GUI automation backend (E12-S2).

The first concrete :class:`~stackowl.tools.gui.base.GuiAdapter`: real screen
capture (``mss`` → PNG) and real input injection (``python-xlib`` XTEST). It
upholds the four base invariants for Linux/X11:

1. **Real attended-display probe** — :meth:`is_available` refuses a headless /
   no-``$DISPLAY`` host AND a *phantom* display (a 0×0 / 1×1 root window, e.g. a
   stray Xvfb): a real attended session has a non-trivial root geometry.
2. **Redact-or-refuse** — the redaction PASS is S5; :meth:`capture` returns the
   raw frame with ``redacted=False`` and never logs the bytes (dims only). The
   tool/S5 performs the redaction before the frame is surfaced.
3. **Gate-before-act** — the safety GATE runs at the tool layer (S6); the adapter
   is the executor. As defence-in-depth it ALSO refuses a hard-blocked key combo
   (reusing the S1 safety data) before injecting it.
4. **Per-OS key translation** — canonical combo tokens → X11 keysym names via
   :mod:`stackowl.tools.gui.xdo_keys`.

Heavy deps (``python-xlib`` / ``mss`` / ``pillow``) are RUNTIME auto-installed on
first use behind a :class:`TestModeGuard` gate (mirroring ``PiperBackend`` /
``DockerSandbox``) — NOT pyproject deps, so ``uv sync`` stays green. SOM degrades
to coordinate-only where AT-SPI is unavailable (``supports_som`` reflects the
host honestly).

Self-healing (B5): probe / capture / perform fail closed to a structured
result — a missing driver, a dropped display, or a bad action never raises.
Cross-platform: on a non-Linux host the adapter is immediately unavailable and no
X11 call is attempted.

Construction seam: the adapter is constructable with an injected
:class:`~stackowl.infra.clock.Clock`; a ``GuiBackend``/selector (E12-S6) will
select it — this story does NOT wire the tool.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess  # noqa: S404 - fixed argv pip install, no shell
import sys
from typing import TYPE_CHECKING

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.tools.gui import xdo_input
from stackowl.tools.gui.base import GuiAdapter, GuiAvailability, GuiPlatform
from stackowl.tools.gui.models import ActionResult, CaptureResult
from stackowl.tools.gui.safety_blocks import BLOCKED_KEY_COMBOS, canon_key_combo
from stackowl.tools.gui.schema import CaptureMode, GuiAction

if TYPE_CHECKING:  # pragma: no cover - typing only
    from Xlib.display import Display

__all__ = ["XdoAdapter"]

# Runtime pip packages (import names differ from dist names): python-xlib→Xlib.
_PIP_PACKAGES = ("python-xlib", "mss", "pillow")
_INSTALL_TIMEOUT_S = 600
# A real attended display has a root window far larger than this; anything at or
# below it (0×0 / 1×1) is treated as a phantom and refused.
_MIN_DISPLAY_DIM = 2


class XdoAdapter(GuiAdapter):
    """Linux/X11 desktop automation via mss (capture) + python-xlib XTEST (input)."""

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or WallClock()
        self._display: Display | None = None
        self._unavailable_reason: str | None = None
        # AT-SPI / accessibility is not installable here → coordinate-only SOM.
        self._supports_som = False
        log.tool.debug("[gui.xdo] init", extra={"_fields": {"platform": self.platform}})

    # ----------------------------------------------------------- capabilities
    @property
    def name(self) -> str:
        return "xdo"

    @property
    def platform(self) -> GuiPlatform:
        return "linux"

    @property
    def supports_som(self) -> bool:
        return self._supports_som

    @property
    def supports_input(self) -> bool:
        return True

    # ------------------------------------------------------------- availability
    async def is_available(self) -> GuiAvailability:
        """Probe for a REAL attended X11 display. Structured result, never raises."""
        if sys.platform != "linux":
            return GuiAvailability.down(f"xdo adapter is Linux-only (host: {sys.platform})")
        if not os.environ.get("DISPLAY"):
            return GuiAvailability.down("no X11 display ($DISPLAY unset) — headless/no attended session")
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._probe_display_sync)
        except Exception as exc:  # defence in depth — _probe already catches.
            log.tool.error("[gui.xdo] is_available: unexpected failure", exc_info=exc)
            return GuiAvailability.down(f"X11 probe failed: {type(exc).__name__}")

    def _probe_display_sync(self) -> GuiAvailability:
        """Open the display, read the root geometry, refuse a phantom. Sync (executor)."""
        try:
            self._ensure_deps()
            display = self._ensure_display()
            geo = display.screen().root.get_geometry()
            width, height = int(geo.width), int(geo.height)
        except Exception as exc:  # missing dep / no display / dropped conn → structured.
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            log.tool.error("[gui.xdo] _probe_display_sync: failed", exc_info=exc)
            return GuiAvailability.down(f"X11 unavailable ({self._unavailable_reason})")
        # Phantom-display guard: a 0×0 / 1×1 root is NOT an attended session.
        if width < _MIN_DISPLAY_DIM or height < _MIN_DISPLAY_DIM:
            log.tool.warning(
                "[gui.xdo] _probe_display_sync: phantom display refused",
                extra={"_fields": {"width": width, "height": height}},
            )
            return GuiAvailability.down(
                f"phantom display refused: root geometry {width}x{height} "
                "is not a real attended session",
            )
        log.tool.debug(
            "[gui.xdo] _probe_display_sync: attended display ok",
            extra={"_fields": {"width": width, "height": height}},
        )
        return GuiAvailability.up()

    def _ensure_deps(self) -> None:
        """Import Xlib/mss/PIL, auto-installing the pip packages once (TestMode-gated)."""
        try:
            import mss  # noqa: F401
            import Xlib.display  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            # No live pip-install in test mode — the TestModeViolation propagates
            # into the caller's broad except → clean structured-unavailable.
            TestModeGuard.assert_not_test_mode("gui.xdo.install")
            log.tool.info(
                "[gui.xdo] _ensure_deps: packages missing — auto-installing",
                extra={"_fields": {"packages": list(_PIP_PACKAGES)}},
            )
            subprocess.run(  # noqa: S603 — fixed argv, no shell.
                [sys.executable, "-m", "pip", "install", *_PIP_PACKAGES],
                check=True,
                capture_output=True,
                timeout=_INSTALL_TIMEOUT_S,
            )
            import mss  # noqa: F401
            import Xlib.display  # noqa: F401
            from PIL import Image  # noqa: F401

    def _ensure_display(self) -> Display:
        """Open (or reuse, self-healing) the X11 display connection."""
        if self._display is not None:
            return self._display
        from Xlib import display as xdisplay

        self._display = xdisplay.Display()
        return self._display

    def _reset_display(self) -> None:
        """Drop a (possibly dead) display handle so the next call reconnects (B5)."""
        if self._display is not None:
            try:
                self._display.close()
            except Exception as exc:  # noqa: BLE001 - best-effort close
                log.tool.debug("[gui.xdo] _reset_display: close failed", extra={"_fields": {"err": str(exc)}})
        self._display = None

    # ----------------------------------------------------------------- capture
    async def capture(self, *, mode: CaptureMode = "som") -> CaptureResult:
        """Grab the screen via mss → PNG bytes. Never logs frame bytes. Never raises."""
        log.tool.debug("[gui.xdo] capture: entry", extra={"_fields": {"mode": mode}})
        loop = asyncio.get_event_loop()
        try:
            frame, width, height = await loop.run_in_executor(None, self._capture_sync)
        except Exception as exc:  # capture failure → structured empty frame (B5).
            log.tool.error("[gui.xdo] capture: failed", exc_info=exc)
            return CaptureResult(frame=b"", width=0, height=0, elements=(), redacted=False)
        # SOM element tree requires AT-SPI (unavailable here) → coordinate-only.
        log.tool.debug(
            "[gui.xdo] capture: exit",
            extra={"_fields": {"width": width, "height": height, "bytes": len(frame), "som": self._supports_som}},
        )
        # redacted=False: the redaction pass is S5; the adapter returns the raw frame.
        return CaptureResult(
            frame=frame, width=width, height=height, elements=(), redacted=False, is_local_vision=False,
        )

    def _capture_sync(self) -> tuple[bytes, int, int]:
        """Grab the primary monitor and PNG-encode it. Sync (executor)."""
        self._ensure_deps()
        import mss
        from PIL import Image

        # ``mss.MSS`` is the modern factory; older mss exposes ``mss.mss`` — prefer
        # the new name, fall back for cross-version robustness across hosts.
        screenshotter = getattr(mss, "MSS", None) or mss.mss
        with screenshotter() as sct:
            monitor = sct.monitors[1]  # [0] is the all-monitors union; [1] is primary.
            shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), int(shot.size[0]), int(shot.size[1])

    # ----------------------------------------------------------------- perform
    async def perform(self, action: GuiAction) -> ActionResult:
        """Execute one action via XTEST. Never logs typed text. Never raises (B5)."""
        log.tool.debug("[gui.xdo] perform: entry", extra={"_fields": {"action": action.action}})
        t0 = self._clock.monotonic()
        # Defence-in-depth: refuse a hard-blocked combo even though the gate is S6.
        blocked = self._blocked_combo_refusal(action)
        if blocked is not None:
            return self._result(False, action.action, blocked, t0)
        loop = asyncio.get_event_loop()
        try:
            detail = await loop.run_in_executor(None, self._perform_sync, action)
            ok = True
        except Exception as exc:  # any input/X11 failure → structured failed (B5).
            self._reset_display()  # self-heal a possibly-dropped connection.
            log.tool.error(
                "[gui.xdo] perform: failed",
                exc_info=exc,
                extra={"_fields": {"action": action.action}},  # NEVER the typed text
            )
            detail, ok = f"{action.action} failed: {type(exc).__name__}", False
        return self._result(ok, action.action, detail, t0)

    def _blocked_combo_refusal(self, action: GuiAction) -> str | None:
        """Return a refusal reason if a ``key`` action is a hard-blocked combo."""
        if action.action != "key" or not action.keys:
            return None
        tokens = canon_key_combo(action.keys)
        for entry in BLOCKED_KEY_COMBOS.get(self.platform, frozenset()):
            if entry and entry.issubset(tokens):
                log.security.warning(
                    "[gui.xdo] perform: refused hard-blocked combo (defence-in-depth)",
                    extra={"_fields": {"combo": sorted(tokens)}},
                )
                return f"key combo {sorted(tokens)} is hard-blocked on {self.platform}"
        return None

    def _perform_sync(self, action: GuiAction) -> str:
        """Dispatch one action to its XTEST primitive. Sync (executor)."""
        return xdo_input.dispatch_action(self._ensure_display(), action)

    def _result(self, ok: bool, action: str, detail: str, t0: float) -> ActionResult:
        duration_ms = (self._clock.monotonic() - t0) * 1000
        log.tool.debug(
            "[gui.xdo] perform: exit",
            extra={"_fields": {"action": action, "ok": ok, "duration_ms": duration_ms}},
        )
        return ActionResult(ok=ok, action=action, detail=detail, duration_ms=duration_ms)
