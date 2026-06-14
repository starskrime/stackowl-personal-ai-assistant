"""Tests for lean behavioral charter, LEAN_WINDOW_THRESHOLD, and the lean kwarg.

Covers:
  - LEAN_WINDOW_THRESHOLD constant value.
  - behavioral_charter_lean() produces a shorter but principle-preserving text.
  - build_base_prompt(now, lean=True) uses the lean charter.
  - build_base_prompt(now) / build_base_prompt(now, lean=False) are byte-identical
    and contain the full charter.
"""

from __future__ import annotations

from datetime import datetime

from stackowl.owls.base_prompt import (
    LEAN_WINDOW_THRESHOLD,
    behavioral_charter,
    behavioral_charter_lean,
    build_base_prompt,
)

_NOW = datetime(2026, 6, 14, 12, 0, 0)


def test_threshold_value() -> None:
    assert LEAN_WINDOW_THRESHOLD == 8192


def test_lean_charter_shorter_but_keeps_principles() -> None:
    full = behavioral_charter()
    lean = behavioral_charter_lean()
    assert 0 < len(lean) < len(full)
    low = lean.lower()
    assert "own" in low
    assert "deliver" in low and ("hand back" in low or "manual" in low or "link" in low)
    assert "persist" in low
    assert "memory" in low


def test_build_base_prompt_lean_uses_lean_charter() -> None:
    assert behavioral_charter_lean() in build_base_prompt(_NOW, lean=True)
    assert behavioral_charter_lean() not in build_base_prompt(_NOW, lean=False)


def test_build_base_prompt_full_byte_identical() -> None:
    full = build_base_prompt(_NOW)
    assert build_base_prompt(_NOW, lean=False) == full
    assert behavioral_charter() in full
