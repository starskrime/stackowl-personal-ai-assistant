"""Per-OS blocked key combos + blocked type patterns + key canonicalisation (E12-S1).

The hard-block DATA and the key-namespace normalisation for the GUI safety gate.
Split from ``safety.py`` to keep each file under the B2 line budget.

Blocked combos are per-OS because a combo only has meaning in the keymap of the
OS that interprets it (translating across keymaps is BUILD work per OS, not a
verbatim port). The Linux/X11 keysym map is BUILT here; the macOS / Windows maps
are intentionally EMPTY stubs the S3 (quartz) / S4 (win32) adapters fill in
their native namespaces.
"""

from __future__ import annotations

import re
from enum import StrEnum


class Os(StrEnum):
    """OS key namespaces for blocked-combo translation."""

    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"


def combo(*keys: str) -> frozenset[str]:
    """Build a canonical (lower-cased) key set for a blocked combo."""
    return frozenset(k.lower() for k in keys)


# Per-OS hard-blocked key combos, each in its OWN native key namespace.
BLOCKED_KEY_COMBOS: dict[str, frozenset[frozenset[str]]] = {
    Os.LINUX.value: frozenset(
        {
            combo("ctrl", "alt", "delete"),  # kills the session / VT switch prompt
            combo("ctrl", "alt", "backspace"),  # zaps the X server
            combo("super", "l"),  # lock screen
            combo("ctrl", "alt", "f1"),  # switch to another VT
            combo("ctrl", "alt", "f2"),
            combo("ctrl", "alt", "f3"),
        },
    ),
    # Filled by E12-S3 (macOS / quartz) in its native namespace.
    Os.MACOS.value: frozenset(),
    # Filled by E12-S4 (Windows / win32) in its native namespace.
    Os.WINDOWS.value: frozenset(),
}

_KEY_ALIASES: dict[str, str] = {
    "control": "ctrl",
    "del": "delete",
    "bksp": "backspace",
    "win": "super",
    "meta": "super",
    "command": "super",
    "cmd": "super",
    "option": "alt",
    "return": "enter",
    "esc": "escape",
}


def canon_key_combo(keys: str) -> frozenset[str]:
    """Normalise a ``ctrl+alt+Del`` style combo string to a canonical key set.

    Multilingual-safe: splits on ``+`` only, lower-cases, applies aliases. Never
    raises — a malformed combo yields the best-effort token set.
    """
    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", keys) if p.strip()]
    return frozenset(_KEY_ALIASES.get(p, p) for p in parts)


# Destructive/credential text patterns refused for the ``type`` action. These
# are case-insensitive defence-in-depth (a denylist), NOT the primary control —
# per-action consent is. Kept neutral (no OS / vendor names).
BLOCKED_TYPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"curl\s+[^|]*\|\s*(?:ba)?sh", re.IGNORECASE),
    re.compile(r"wget\s+[^|]*\|\s*(?:ba)?sh", re.IGNORECASE),
    re.compile(r"\bsudo\s+rm\s+-[rf]", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+/", re.IGNORECASE),
    re.compile(r":\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}", re.IGNORECASE),  # fork bomb
    re.compile(r"mkfs\.\w+\s+/dev/", re.IGNORECASE),
)
