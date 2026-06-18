"""Story 8 — every TUI localize() key resolves to real text (no raw-key leaks)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stackowl.tui.i18n import clear_translations, localize
from stackowl.tui.i18n_strings import _EN, install_default_translations

pytestmark = pytest.mark.tui

_TUI_SRC = (
    Path(__file__).resolve().parents[2] / "src" / "stackowl" / "tui"
)
_LOCALIZE_LITERAL = re.compile(r'localize\(\s*"([^"]+)"\s*\)')

# Keys passed to localize() via a variable (not a string literal) — the static
# scan can't see these, so they are enumerated explicitly.
_DYNAMIC_KEYS: frozenset[str] = frozenset({"compose.parliament_active"})


def _used_keys() -> set[str]:
    keys: set[str] = set(_DYNAMIC_KEYS)
    for path in _TUI_SRC.rglob("*.py"):
        keys.update(_LOCALIZE_LITERAL.findall(path.read_text(encoding="utf-8")))
    return keys


def test_every_used_key_is_registered() -> None:
    """No localize() call may fall through to rendering its raw key."""
    missing = sorted(k for k in _used_keys() if k not in _EN)
    assert not missing, f"Unregistered TUI i18n keys (would render raw): {missing}"


def test_install_resolves_keys_to_real_text() -> None:
    clear_translations()
    install_default_translations()
    for key, expected in _EN.items():
        assert localize(key) == expected
        assert localize(key) != key  # real text, not the bare key
