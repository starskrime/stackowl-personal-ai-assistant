"""Tests for the xdo (Linux/X11) GuiAdapter (E12-S2).

Two tiers:

* **Unit** (no display, no install): the real-attended-display probe rejecting
  no-display / phantom-1x1 and accepting a real geometry (mocked); TestModeGuard
  blocking the install; per-OS X11 keysym translation; the defence-in-depth
  blocked-combo refusal; never-raises on a bad action / no display.
* **Integration against a SCRATCH Xvfb** (NEVER the user's real ``:0``): start a
  throwaway ``Xvfb :N``, ``capture()`` it (non-empty PNG) and ``perform`` a
  ``move`` + ``click`` (assert via Xlib that the pointer warped). SKIPS with an
  honest reason if Xvfb / the deps (python-xlib / mss / pillow) are unavailable —
  it never falsely claims a guarantee the host can't provide.

Safety: input is ONLY ever driven against the scratch Xvfb display, never ``:0``.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.tools.gui import xdo_keys
from stackowl.tools.gui.schema import GuiAction
from stackowl.tools.gui.xdo import XdoAdapter

# ---- dep / display availability probes (drive the honest skips) -------------
_DEPS_OK = all(importlib.util.find_spec(m) is not None for m in ("Xlib", "mss", "PIL"))
_XVFB = shutil.which("Xvfb")


def _make_action(**kw: Any) -> GuiAction:
    return GuiAction(**kw)


# ==================================================================== UNIT ====
class _FakeGeo:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height


class _FakeRoot:
    def __init__(self, geo: _FakeGeo) -> None:
        self._geo = geo

    def get_geometry(self) -> _FakeGeo:
        return self._geo


class _FakeScreen:
    def __init__(self, geo: _FakeGeo) -> None:
        self.root = _FakeRoot(geo)


class _FakeDisplay:
    def __init__(self, geo: _FakeGeo) -> None:
        self._geo = geo

    def screen(self) -> _FakeScreen:
        return _FakeScreen(self._geo)

    def close(self) -> None:  # pragma: no cover - reset path
        pass


def _patch_probe(monkeypatch: pytest.MonkeyPatch, adapter: XdoAdapter, geo: _FakeGeo) -> None:
    """Make the probe skip deps + return a fake display with the given geometry."""
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(adapter, "_ensure_deps", lambda: None)
    monkeypatch.setattr(adapter, "_ensure_display", lambda: _FakeDisplay(geo))


@pytest.mark.asyncio
async def test_is_available_no_display(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    avail = await XdoAdapter().is_available()
    assert avail.available is False
    assert "DISPLAY" in (avail.reason or "")


@pytest.mark.asyncio
async def test_is_available_phantom_1x1_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = XdoAdapter()
    _patch_probe(monkeypatch, adapter, _FakeGeo(1, 1))
    avail = await adapter.is_available()
    assert avail.available is False
    assert "phantom" in (avail.reason or "").lower()


@pytest.mark.asyncio
async def test_is_available_real_display_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = XdoAdapter()
    _patch_probe(monkeypatch, adapter, _FakeGeo(1920, 1080))
    avail = await adapter.is_available()
    assert avail.available is True
    assert avail.reason is None


@pytest.mark.asyncio
async def test_is_available_non_linux_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stackowl.tools.gui.xdo.sys.platform", "darwin")
    avail = await XdoAdapter().is_available()
    assert avail.available is False
    assert "linux" in (avail.reason or "").lower()


@pytest.mark.asyncio
async def test_is_available_testmode_no_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under test mode the install is blocked → structured unavailable, no crash."""
    monkeypatch.setenv("DISPLAY", ":99")

    def _raise_import(*_: Any, **__: Any) -> Any:
        raise ImportError("Xlib missing")

    # Force the dep import to fail so _ensure_deps hits the TestModeGuard path.
    monkeypatch.setattr("stackowl.tools.gui.xdo.subprocess.run", _raise_import)
    TestModeGuard.activate()
    try:
        avail = await XdoAdapter().is_available()
    finally:
        TestModeGuard.deactivate()
    assert avail.available is False


@pytest.mark.asyncio
async def test_capture_never_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = XdoAdapter()

    def _boom() -> Any:
        raise RuntimeError("no screen")

    monkeypatch.setattr(adapter, "_capture_sync", _boom)
    result = await adapter.capture()
    assert result.frame == b""
    assert result.width == 0 and result.height == 0
    assert result.redacted is False  # S5 does redaction; capture returns raw


@pytest.mark.asyncio
async def test_perform_never_raises_bad_target() -> None:
    """A click without a resolvable pixel target fails structurally, never raises."""
    adapter = XdoAdapter()
    # element id with no SOM/AT-SPI → cannot resolve → failed result, not a crash.
    action = _make_action(action="click", element=3)
    result = await adapter.perform(action)
    assert result.ok is False
    assert result.action == "click"


@pytest.mark.asyncio
async def test_perform_refuses_blocked_combo() -> None:
    """Defence-in-depth: a hard-blocked combo is refused at the adapter too."""
    adapter = XdoAdapter()
    result = await adapter.perform(_make_action(action="key", keys="ctrl+alt+delete"))
    assert result.ok is False
    assert "blocked" in result.detail.lower()


# --- per-OS X11 keysym translation (pure, no Xlib) ---------------------------
def test_keysym_translation_ctrl_s() -> None:
    mods, primary = xdo_keys.combo_to_keysym_names("ctrl+s")
    assert mods == ("Control_L",)
    assert primary == ("s",)


def test_keysym_translation_aliases_and_named_keys() -> None:
    # cmd/win → super; return → enter → Return; named function key.
    mods, primary = xdo_keys.combo_to_keysym_names("cmd+shift+return")
    assert "Super_L" in mods and "Shift_L" in mods
    assert primary == ("Return",)
    assert xdo_keys.token_to_keysym_name("f5") == "F5"
    assert xdo_keys.token_to_keysym_name("escape") == "Escape"


# ============================================================ XVFB INTEGRATION =
@contextlib.contextmanager
def _scratch_xvfb(display_num: int = 97) -> Iterator[str]:
    """Start a throwaway Xvfb on a NON-:0 display; tear it down. NEVER touches :0."""
    disp = f":{display_num}"
    proc = subprocess.Popen(  # noqa: S603 - fixed argv
        [str(_XVFB), disp, "-screen", "0", "1024x768x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1.5)  # let the server come up
        yield disp
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)


@pytest.mark.skipif(not (_DEPS_OK and _XVFB), reason="needs python-xlib/mss/pillow + Xvfb on host")
@pytest.mark.asyncio
async def test_capture_against_scratch_xvfb(monkeypatch: pytest.MonkeyPatch) -> None:
    with _scratch_xvfb(97) as disp:
        monkeypatch.setenv("DISPLAY", disp)
        adapter = XdoAdapter()
        avail = await adapter.is_available()
        assert avail.available is True, avail.reason
        cap = await adapter.capture()
        assert cap.width > 0 and cap.height > 0
        assert cap.frame.startswith(b"\x89PNG")  # real PNG bytes
        assert len(cap.frame) > 100


@pytest.mark.skipif(not (_DEPS_OK and _XVFB), reason="needs python-xlib/mss/pillow + Xvfb on host")
@pytest.mark.asyncio
async def test_perform_move_and_click_against_scratch_xvfb(monkeypatch: pytest.MonkeyPatch) -> None:
    with _scratch_xvfb(98) as disp:
        monkeypatch.setenv("DISPLAY", disp)
        adapter = XdoAdapter()
        assert (await adapter.is_available()).available is True

        move = await adapter.perform(_make_action(action="move", x=300, y=240))
        assert move.ok is True, move.detail

        # Verify the pointer actually warped (read it back via Xlib on the scratch).
        from Xlib import display as xdisplay

        d = xdisplay.Display(disp)
        try:
            pointer = d.screen().root.query_pointer()
            assert pointer.root_x == 300 and pointer.root_y == 240
        finally:
            d.close()

        click = await adapter.perform(_make_action(action="click", x=150, y=120))
        assert click.ok is True, click.detail


@pytest.mark.skipif(not (_DEPS_OK and _XVFB), reason="needs python-xlib/mss/pillow + Xvfb on host")
@pytest.mark.asyncio
async def test_perform_key_combo_against_scratch_xvfb(monkeypatch: pytest.MonkeyPatch) -> None:
    with _scratch_xvfb(96) as disp:
        monkeypatch.setenv("DISPLAY", disp)
        adapter = XdoAdapter()
        assert (await adapter.is_available()).available is True
        # A benign combo translates to real keysyms and is injected without error.
        res = await adapter.perform(_make_action(action="key", keys="ctrl+a"))
        assert res.ok is True, res.detail


if os.environ.get("_XDO_TEST_NOTE"):  # pragma: no cover - diagnostic only
    print(f"deps_ok={_DEPS_OK} xvfb={_XVFB}")
