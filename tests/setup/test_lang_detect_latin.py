"""I18N-1 (E-I18N) — Latin-script disambiguation for de/fr/es vs en.

The coarse ``unicodedata`` script detector collapses every Latin-script language
to ``"en"`` (a documented cut). That meant a French/Spanish/German user hitting
the provider-down honest floor got an English message even though the localize
catalog already carries de/fr/es translations.

This adds a STDLIB-ONLY orthographic heuristic (diacritics + language-specific
glyphs — NOT a hardcoded English keyword list, no third-party lingua-py dep that
breaks on ARM64/Jetson) so the dominant Latin language is disambiguated when its
characteristic glyphs are present, while plain ASCII still degrades to ``"en"``.

Mandates respected:
  * NO hardcoded English keywords — the signal is Unicode orthography (accented
    letters / language-specific punctuation), never an English stopword list.
  * NO fragile external dependency — pure ``unicodedata``/stdlib, never raises.
"""

from __future__ import annotations

import pytest

from stackowl.setup.lang_detect import detect_language


# --------------------------------------------------------------------------- #
# Non-Latin scripts are UNCHANGED (regression guard).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Привет, как дела?", "ru"),
        ("こんにちは世界", "ja"),
        ("你好世界", "zh"),
        ("مرحبا بالعالم", "ar"),
    ],
)
def test_non_latin_unchanged(text: str, expected: str) -> None:
    assert detect_language(text) == expected


# --------------------------------------------------------------------------- #
# Plain ASCII Latin still degrades to en (no false positives).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "Hello world, please book a meeting for tomorrow.",
        "The quick brown fox jumps over the lazy dog.",
        "",
        "12345 !!! ???",
    ],
)
def test_plain_ascii_is_en(text: str) -> None:
    assert detect_language(text) == "en"


# --------------------------------------------------------------------------- #
# de/fr/es no longer collapse to en when their orthographic glyphs appear.
# --------------------------------------------------------------------------- #
def test_german_detected() -> None:
    # ß + ä/ö/ü are German-exclusive among the supported set.
    assert detect_language("Könnten Sie bitte für mich ein Meeting buchen? Größe.") == "de"


def test_french_detected() -> None:
    # ç + ê/è/œ are strong French signals.
    assert detect_language("Pourriez-vous réserver une réunion, s'il vous plaît ? Garçon.") == "fr"


def test_spanish_detected() -> None:
    # ñ + inverted ¿ ¡ are Spanish-exclusive.
    assert detect_language("¿Podrías reservar una reunión para mañana? ¡Señor!") == "es"


def test_never_raises_on_junk() -> None:
    assert detect_language("\x00\x01\x02") == "en"
