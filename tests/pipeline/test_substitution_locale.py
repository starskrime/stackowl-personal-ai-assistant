"""REACT-4 / F030 — the self-heal substitution note honors the user's locale.

The route-around observation is prefixed with a localized note. Previously the
locale was hardcoded ``"en"`` so a German user got an English note. The note must
be localized from ``state.language`` (the coarse tag triage stamps), falling back
to en for an uncatalogued language.
"""
from __future__ import annotations

import pytest
from tests.pipeline.test_dispatch_substitution import _build_real_dispatch, _web_tools


@pytest.mark.asyncio
async def test_substitution_note_uses_state_language(monkeypatch):
    """A German turn (state.language='de') gets the German self-heal note, not en."""
    from stackowl.pipeline.steps import execute as exe
    from stackowl.setup.localize import localize_format

    reg = _web_tools()
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg, language="de")

    out = await dispatch("browser_browse", {"task": "wetter heute"})

    de_note = localize_format("self_heal_substituted", "de", failed="browser_browse", sibling="web_search")
    en_note = localize_format("self_heal_substituted", "en", failed="browser_browse", sibling="web_search")
    assert de_note != en_note  # guard: the fixture actually differs by locale
    assert de_note in out
    assert en_note not in out


@pytest.mark.asyncio
async def test_substitution_note_defaults_to_en_for_unknown_language(monkeypatch):
    """An uncatalogued language falls back to the en note (localize en-fallback)."""
    from stackowl.pipeline.steps import execute as exe
    from stackowl.setup.localize import localize_format

    reg = _web_tools()
    dispatch = await _build_real_dispatch(monkeypatch, exe, reg, language="zxx")

    out = await dispatch("browser_browse", {"task": "weather today"})

    en_note = localize_format("self_heal_substituted", "en", failed="browser_browse", sibling="web_search")
    assert en_note in out
