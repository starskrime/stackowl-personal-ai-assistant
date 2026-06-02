"""XTEST input primitives for the xdo adapter (E12-S2) — synchronous Xlib I/O.

All real X11 input injection lives here so :mod:`stackowl.tools.gui.xdo` stays a
thin orchestrator under the B2 budget. These are SYNCHRONOUS primitives (Xlib is
blocking); the adapter drives them via ``run_in_executor`` so the event loop is
never blocked.

Security/observability: this module NEVER logs typed text — the caller passes
already-validated content and we emit no field carrying it. Each primitive
operates on an injected open ``Xlib.display.Display`` so the adapter owns the
connection lifecycle (and can self-heal a dropped one by reconnecting).

Cross-platform: this module imports Xlib at call time only; it is never imported
on a non-Linux host (the adapter guards that), and a missing/invalid keysym is a
structured failure raised to the adapter's broad except — never a crash here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stackowl.tools.gui import xdo_keys
from stackowl.tools.gui.schema import GuiAction

if TYPE_CHECKING:  # pragma: no cover - typing only
    from Xlib.display import Display

__all__ = [
    "dispatch_action",
    "drag",
    "key_combo",
    "move_pointer",
    "press_release",
    "scroll",
    "type_text",
]

# X11 pointer button numbers.
_BTN_LEFT = 1
_BTN_RIGHT = 3
_BTN_SCROLL_UP = 4
_BTN_SCROLL_DOWN = 5
_BTN_SCROLL_LEFT = 6
_BTN_SCROLL_RIGHT = 7

_SCROLL_BUTTON: dict[str, int] = {
    "up": _BTN_SCROLL_UP,
    "down": _BTN_SCROLL_DOWN,
    "left": _BTN_SCROLL_LEFT,
    "right": _BTN_SCROLL_RIGHT,
}


def _xtest() -> Any:
    from Xlib.ext import xtest

    return xtest


def _x() -> Any:
    from Xlib import X

    return X


def _keysym_name_to_keycode(display: Display, name: str) -> int:
    """Resolve an X11 keysym NAME → keycode for the current keymap.

    Raises ``ValueError`` if the keysym name is unknown or unmapped on the active
    keymap (the adapter converts that to a structured failed ActionResult).
    """
    from Xlib import XK

    keysym = XK.string_to_keysym(name)
    if keysym == 0:
        raise ValueError(f"unknown X11 keysym name: {name!r}")
    keycode = display.keysym_to_keycode(keysym)
    if not keycode:
        raise ValueError(f"keysym {name!r} is not mapped on the active keymap")
    return int(keycode)


def move_pointer(display: Display, x: int, y: int) -> None:
    """Warp the pointer to absolute screen coords (x, y) via XTEST."""
    _xtest().fake_input(display, _x().MotionNotify, x=int(x), y=int(y))
    display.sync()


def press_release(
    display: Display,
    x: int,
    y: int,
    *,
    button: int = _BTN_LEFT,
    count: int = 1,
) -> None:
    """Move to (x, y) then press+release ``button`` ``count`` times (click)."""
    move_pointer(display, x, y)
    x_const = _x()
    xt = _xtest()
    for _ in range(max(1, count)):
        xt.fake_input(display, x_const.ButtonPress, button)
        xt.fake_input(display, x_const.ButtonRelease, button)
    display.sync()


def drag(
    display: Display,
    src: tuple[int, int],
    dst: tuple[int, int],
    *,
    button: int = _BTN_LEFT,
) -> None:
    """Press at ``src``, move to ``dst``, release — a primary-button drag."""
    x_const = _x()
    xt = _xtest()
    move_pointer(display, *src)
    xt.fake_input(display, x_const.ButtonPress, button)
    display.sync()
    move_pointer(display, *dst)
    xt.fake_input(display, x_const.ButtonRelease, button)
    display.sync()


def scroll(display: Display, x: int, y: int, direction: str, amount: int) -> None:
    """Scroll at (x, y) by emitting ``amount`` button-4/5/6/7 click events."""
    button = _SCROLL_BUTTON.get(direction)
    if button is None:
        raise ValueError(f"unsupported scroll direction: {direction!r}")
    press_release(display, x, y, button=button, count=max(1, amount))


def key_combo(
    display: Display,
    modifier_names: tuple[str, ...],
    primary_names: tuple[str, ...],
) -> None:
    """Hold the modifier keysyms, tap the primary keysyms, release modifiers.

    Names are X11 keysym NAMES (from
    :func:`stackowl.tools.gui.xdo_keys.combo_to_keysym_names`). An unresolved
    keysym raises ``ValueError`` BEFORE any key is pressed, so a bad combo never
    leaves a modifier stuck down.
    """
    x_const = _x()
    xt = _xtest()
    mods = [_keysym_name_to_keycode(display, n) for n in modifier_names]
    primaries = [_keysym_name_to_keycode(display, n) for n in primary_names]
    for code in mods:
        xt.fake_input(display, x_const.KeyPress, code)
    try:
        for code in primaries:
            xt.fake_input(display, x_const.KeyPress, code)
            xt.fake_input(display, x_const.KeyRelease, code)
    finally:
        for code in reversed(mods):
            xt.fake_input(display, x_const.KeyRelease, code)
        display.sync()


def type_text(display: Display, text: str) -> None:
    """Type ``text`` by mapping each char to a keysym and tapping it.

    NEVER logs the text. Each character becomes its own keysym name (a printable
    char IS its keysym name for the common case); a char with no mappable keysym
    is skipped rather than aborting the whole string (best-effort typing). Shift
    is applied for characters whose keycode resolves only with shift is NOT
    attempted here — uppercase/symbol shifting relies on the active keymap via
    the char's own keysym name where one exists.
    """
    from Xlib import XK

    x_const = _x()
    xt = _xtest()
    for ch in text:
        keysym = XK.string_to_keysym(ch)
        if keysym == 0:
            # Try the X11 convention name for whitespace etc.
            keysym = XK.string_to_keysym({"\n": "Return", "\t": "Tab", " ": "space"}.get(ch, ch))
        if keysym == 0:
            continue
        keycode = display.keysym_to_keycode(keysym)
        if not keycode:
            continue
        xt.fake_input(display, x_const.KeyPress, keycode)
        xt.fake_input(display, x_const.KeyRelease, keycode)
    display.sync()


# --------------------------------------------------------------------- dispatch
_CLICK_BUTTON: dict[str, int] = {"click": _BTN_LEFT, "double_click": _BTN_LEFT, "right_click": _BTN_RIGHT}


def _point(action: GuiAction) -> tuple[int, int]:
    """Resolve the primary target to pixels.

    With no SOM tree (coordinate-only on this host), an element-id target cannot
    be resolved → ``ValueError`` (the adapter turns it into a failed result).
    """
    if action.x is not None and action.y is not None:
        return action.x, action.y
    raise ValueError("element targeting requires SOM/AT-SPI (coordinate-only here): supply x,y pixels")


def _dest_point(action: GuiAction) -> tuple[int, int]:
    if action.to_x is not None and action.to_y is not None:
        return action.to_x, action.to_y
    raise ValueError("drag destination requires SOM/AT-SPI (coordinate-only here): supply to_x,to_y")


def dispatch_action(display: Display, action: GuiAction) -> str:
    """Execute one :class:`GuiAction` via the XTEST primitives; return a detail.

    NEVER logs / returns the typed text. ``set_value`` needs an AT-SPI write path
    (unavailable without SOM) → a structured ``ValueError``. Raises on any failure
    so the adapter's broad except converts it to a failed ActionResult (B5).
    """
    name = action.action
    if name in _CLICK_BUTTON:
        x, y = _point(action)
        press_release(display, x, y, button=_CLICK_BUTTON[name], count=2 if name == "double_click" else 1)
        return f"{name} at point"
    if name == "move":
        move_pointer(display, *_point(action))
        return "moved pointer"
    if name == "drag":
        drag(display, _point(action), _dest_point(action))
        return "dragged"
    if name == "scroll":
        x, y = _point(action)
        scroll(display, x, y, action.direction or "down", action.amount)
        return f"scrolled {action.direction}"
    if name == "type":
        type_text(display, action.text or "")  # text NEVER logged
        return "typed text"
    if name == "key":
        modifiers, primary = xdo_keys.combo_to_keysym_names(action.keys or "")
        key_combo(display, modifiers, primary)
        return "sent key combo"
    if name == "set_value":
        raise ValueError("set_value requires an accessibility (AT-SPI) write path, unavailable on this host")
    if name == "capture":
        raise ValueError("capture is performed via capture(), not perform()")
    raise ValueError(f"unsupported action: {name}")
