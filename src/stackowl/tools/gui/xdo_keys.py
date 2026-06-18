"""Per-OS key translation for the X11/xdo adapter (E12-S2).

INVARIANT #4 (per-OS key translation) realised for Linux/X11: the canonical
combo tokens the model speaks (``ctrl`` / ``alt`` / ``super`` / ``shift`` plus a
key) are mapped to X11 **keysym names**, which Xlib then resolves to keysyms and
keycodes at perform time. This is the BUILD (not a verbatim port): the mapping
lives in the X11 keysym namespace and is the inverse of the blocked-combo data
in :mod:`stackowl.tools.gui.safety_blocks`.

Pure + never raises: an unknown token yields a best-effort capitalised X11
keysym-name guess so a typo degrades to "no such keysym" at lookup, not a crash.
No Xlib import here — this module is pure data/string translation so it is unit
testable without a display.
"""

from __future__ import annotations

from stackowl.tools.gui.safety_blocks import canon_key_combo

__all__ = ["MODIFIER_TOKENS", "combo_to_keysym_names", "token_to_keysym_name"]

# Canonical modifier tokens (post-:func:`canon_key_combo` normalisation). These
# map to the *_L (left) X11 modifier keysym names — the conventional choice for
# synthetic input.
MODIFIER_TOKENS: frozenset[str] = frozenset({"ctrl", "alt", "super", "shift"})

# Canonical token → X11 keysym NAME (resolved to a keysym via Xlib.XK at perform
# time). Covers the modifiers plus the common named keys the canon layer emits.
_TOKEN_TO_KEYSYM_NAME: dict[str, str] = {
    # modifiers (left variants)
    "ctrl": "Control_L",
    "alt": "Alt_L",
    "super": "Super_L",
    "shift": "Shift_L",
    # whitespace / editing
    "enter": "Return",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete",
    "escape": "Escape",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    # arrows
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    # function keys
    **{f"f{n}": f"F{n}" for n in range(1, 25)},
}


def token_to_keysym_name(token: str) -> str:
    """Map one canonical token to its X11 keysym NAME (best-effort, never raises).

    A single printable character (``a``, ``5``, ``/``) is already a valid X11
    keysym name, so it passes through unchanged. A known named token is mapped via
    the table. An unknown multi-char token is title-cased as a last-resort guess
    (``foo`` → ``Foo``) so the failure surfaces as an unresolved keysym at lookup.
    """
    if token in _TOKEN_TO_KEYSYM_NAME:
        return _TOKEN_TO_KEYSYM_NAME[token]
    if len(token) == 1:
        # A lone printable char IS its keysym name (digits/letters/punct).
        return token
    return token.title()


def combo_to_keysym_names(keys: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a combo string into (modifier keysym names, primary keysym names).

    Uses the shared :func:`canon_key_combo` so the alias/normalisation rules are
    identical to the safety gate (defence-in-depth parity). Returns two tuples:
    the modifier keysym names to hold, and the non-modifier ("primary") keysym
    names to tap while they are held. Pure; never raises.
    """
    tokens = canon_key_combo(keys)
    modifiers = tuple(
        token_to_keysym_name(t) for t in sorted(tokens) if t in MODIFIER_TOKENS
    )
    primary = tuple(
        token_to_keysym_name(t) for t in sorted(tokens) if t not in MODIFIER_TOKENS
    )
    return modifiers, primary
