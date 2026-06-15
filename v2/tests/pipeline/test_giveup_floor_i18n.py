"""F089 + F098 — the deterministic honest floor localizes to the turn's language.

PipelineState had no language field, so ``surface_consequential_giveup_floor`` and
the critical-failure neutral last-resort were English-only — exactly when providers
are down and the LLM cascade can't re-derive the user's language. This threads a
``state.language`` (stamped in triage via a stdlib script detector) into
``synthesize_floor`` and the neutral fallback. Default ``"en"`` = byte-identical.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.supervisor import synthesize_floor
from stackowl.setup.lang_detect import detect_language
from stackowl.setup.localize import localize


# =========================================================================== #
# P-A — PipelineState carries a language field, default "en", survives .evolve()
# =========================================================================== #


def test_state_language_defaults_to_en() -> None:
    state = PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="default", pipeline_step="triage",
    )
    assert state.language == "en"


def test_state_language_survives_evolve() -> None:
    state = PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="default", pipeline_step="triage", language="ru",
    )
    evolved = state.evolve(owl_name="x")
    assert evolved.language == "ru"


# =========================================================================== #
# P-B — coarse stdlib script detector (no third-party dep, never raises)
# =========================================================================== #


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Привет, как дела?", "ru"),  # Cyrillic
        ("こんにちは世界", "ja"),  # Hiragana
        ("你好世界", "zh"),  # Han
        ("مرحبا بالعالم", "ar"),  # Arabic
        ("Hello world", "en"),  # Latin → en
        ("", "en"),  # empty → fail-safe en
    ],
)
def test_detect_language_by_script(text: str, expected: str) -> None:
    assert detect_language(text) == expected


def test_detect_language_never_raises() -> None:
    # Surrogate-ish / control junk must not crash the detector (fail-safe en).
    assert detect_language("\x00\x01\x02") == "en"


# =========================================================================== #
# F089/F098 — synthesize_floor localizes (German is in the catalog)
# =========================================================================== #


def test_floor_localizes_to_german() -> None:
    floor = synthesize_floor(
        goal="Buche ein Meeting",
        error="provider down",
        attempts=["create_event"],
        partial=None,
        failed_capability="create_event",
        lang="de",
    )
    # The German template's stable lead-in phrase appears; the English one does not.
    assert "Ich konnte dies nicht vollständig erledigen" in floor
    assert "I couldn't fully complete this" not in floor


def test_floor_default_is_english_byte_identical() -> None:
    en = synthesize_floor(
        goal="g", error="e", attempts=["a"], partial=None, failed_capability="a"
    )
    assert "I couldn't fully complete this" in en


# =========================================================================== #
# Catalog enrichment — fr/es now exist for the floor templates
# =========================================================================== #


@pytest.mark.parametrize("lang", ["fr", "es"])
@pytest.mark.parametrize("key", ["self_heal_floor", "self_heal_floor_minimal"])
def test_floor_catalog_has_fr_es(key: str, lang: str) -> None:
    text = localize(key, lang)
    # localize falls back to en if missing — assert we got a DISTINCT non-en string.
    assert text != localize(key, "en"), f"{key}/{lang} missing from catalog"
