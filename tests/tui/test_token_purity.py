"""Token purity: no literal colors outside the canonical stackowl.tcss declaration file."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.tui

_STYLES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "stackowl"
    / "tui"
    / "styles"
)

_CANONICAL = "stackowl.tcss"

_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RGB_RE = re.compile(r"rgba?\s*\(")


def _strip_comments(text: str) -> str:
    # Strip /* ... */ comments — those legitimately can include hex examples.
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _iter_tcss(root: Path):
    yield from root.rglob("*.tcss")


def test_no_literal_colors_outside_canonical_tcss() -> None:
    violations: list[str] = []
    for path in _iter_tcss(_STYLES_DIR):
        if path.name == _CANONICAL:
            continue
        body = _strip_comments(path.read_text(encoding="utf-8"))
        for match in _HEX_RE.finditer(body):
            violations.append(f"{path}: hex literal {match.group(0)!r}")
        if _RGB_RE.search(body):
            violations.append(f"{path}: rgb(...) literal found")
    assert not violations, (
        "Use $color-* tokens instead of literal colors:\n" + "\n".join(violations)
    )


def test_canonical_tcss_defines_at_least_one_color() -> None:
    """Sanity check — the canonical file must actually define hex tokens."""
    canonical = _STYLES_DIR / _CANONICAL
    body = _strip_comments(canonical.read_text(encoding="utf-8"))
    assert _HEX_RE.search(body), "stackowl.tcss is expected to declare hex tokens"
