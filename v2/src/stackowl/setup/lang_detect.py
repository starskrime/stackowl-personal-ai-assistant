"""lang_detect — coarse, stdlib-only script + Latin-orthography language detection.

A pure ``unicodedata`` classifier with two layers:

1. NON-LATIN SCRIPT (F089/F098) — counts the Unicode script block of the LETTER
   code points and returns a coarse tag for the dominant non-Latin script (``ru``
   Cyrillic, ``zh`` Han, ``ja`` Hiragana/Katakana, ``ar`` Arabic, ``el`` Greek,
   ``he`` Hebrew, ``hi`` Devanagari, ``ko`` Hangul, ``th`` Thai).

2. LATIN ORTHOGRAPHY (I18N-1 / E-I18N) — when the dominant script is Latin, a
   stdlib-only orthographic scorer disambiguates de/fr/es from en using each
   language's *characteristic glyphs* (diacritics + language-specific
   punctuation). The dominant Latin language wins only when its glyphs actually
   appear; plain ASCII text scores zero everywhere and degrades to ``"en"``.

   This is NOT a hardcoded English keyword list (hard mandate): the signal is the
   presence of Unicode accented letters / language-specific punctuation, never an
   English (or any-language) word list. English is simply "Latin with no
   distinguishing diacritics" — the fail-safe default, asserted by no positive
   keyword set.

DELIBERATE COARSENESS: only de/fr/es are disambiguated (they have catalog
translations for the honest floor). Other Latin languages (it/pt/…) without a
catalog entry still degrade to ``"en"`` honestly — fine-grained detection of
those is the live LLM cascade's job, not this provider-down failure-path helper.

Never raises (fail-safe ``"en"``): this runs on the hot triage path before every
turn and must never crash it. No third-party dependency (ARM64/Jetson-safe; a
``lingua-py`` follow-up would add a fragile native dep and is intentionally NOT
taken — stdlib orthography covers the supported catalog languages).
"""

from __future__ import annotations

import unicodedata

from stackowl.infra.observability import log

_DEFAULT_LANG = "en"

# Substring tokens that appear in ``unicodedata.name(ch)`` for a letter, mapped to a
# coarse language tag. Checked in order; the first match for a letter wins. Latin is
# intentionally absent → Latin-script letters fall through to the default ("en").
_SCRIPT_TOKENS: tuple[tuple[str, str], ...] = (
    ("CYRILLIC", "ru"),
    ("HIRAGANA", "ja"),
    ("KATAKANA", "ja"),
    ("CJK", "zh"),  # Han ideographs — name carries "CJK UNIFIED IDEOGRAPH"
    ("HANGUL", "ko"),
    ("ARABIC", "ar"),
    ("HEBREW", "he"),
    ("GREEK", "el"),
    ("DEVANAGARI", "hi"),
    ("THAI", "th"),
)


# Latin-orthography signals (I18N-1). Each tag maps to the set of characteristic
# glyphs that, when present, distinguish that language from plain-ASCII English.
# These are ORTHOGRAPHIC code points (diacritics + language-specific punctuation),
# NOT word lists — English remains the diacritic-free default with no positive set.
#
#   de — ß (eszett) and umlauts ä ö ü are the German-exclusive signal.
#   fr — ç, the circumflex/grave family (â ê î ô û à è ì ò ù), ë ï ü, and œ/æ.
#   es — ñ plus the inverted ¿ ¡ are Spanish-exclusive.
#
# Vowels shared across languages (e.g. é appears in both fr and es) are weighted
# so that the *exclusive* glyph for each language dominates a tie.
_LATIN_EXCLUSIVE: tuple[tuple[str, frozenset[str]], ...] = (
    ("de", frozenset("ßäöüÄÖÜ")),
    ("fr", frozenset("çàâèêëîïôûùœæÇÀÂÈÊËÎÏÔÛÙŒÆ")),
    ("es", frozenset("ñ¿¡Ñ")),
)
# Accented vowels shared by fr/es — count as a weak signal toward whichever
# language already has an exclusive hit (never decides on their own).
_LATIN_SHARED_ACCENTS: frozenset[str] = frozenset("áéíóúÁÉÍÓÚ")


def _detect_latin(text: str) -> str:
    """Disambiguate de/fr/es from en by characteristic orthography.

    Returns the dominant Latin language tag, or ``"en"`` when no distinguishing
    glyph appears (plain ASCII / unsupported Latin language). Pure, never raises.
    """
    scores: dict[str, int] = {"de": 0, "fr": 0, "es": 0}
    for ch in text:
        for tag, glyphs in _LATIN_EXCLUSIVE:
            if ch in glyphs:
                scores[tag] += 2  # exclusive glyph — strong signal
    # Shared accents nudge fr/es only if one of them already scored exclusively.
    if scores["fr"] or scores["es"]:
        shared = sum(1 for ch in text if ch in _LATIN_SHARED_ACCENTS)
        leader = "fr" if scores["fr"] >= scores["es"] else "es"
        scores[leader] += min(shared, 1)
    best_tag = max(scores, key=lambda t: scores[t])
    if scores[best_tag] == 0:
        return _DEFAULT_LANG  # no distinguishing glyph → English (the default)
    log.setup.debug(
        "lang_detect._detect_latin: exit",
        extra={"_fields": {"detected": best_tag, "scores": scores}},
    )
    return best_tag


def detect_language(text: str | None) -> str:
    """Return a coarse language tag for *text* by dominant non-Latin script.

    Latin / unknown / empty → ``"en"``. Never raises.
    """
    log.setup.debug(
        "lang_detect.detect_language: entry",
        extra={"_fields": {"text_len": len(text) if text else 0}},
    )
    if not text:
        return _DEFAULT_LANG
    counts: dict[str, int] = {}
    try:
        for ch in text:
            if not ch.isalpha():
                continue
            try:
                name = unicodedata.name(ch)
            except ValueError:
                continue  # unnamed code point — skip, never crash
            for token, tag in _SCRIPT_TOKENS:
                if token in name:
                    counts[tag] = counts.get(tag, 0) + 1
                    break
    except Exception as exc:  # noqa: BLE001 — fail-safe: never crash the triage path
        log.setup.warning(
            "lang_detect.detect_language: classification failed — defaulting to en",
            extra={"_fields": {"error": str(exc)}},
        )
        return _DEFAULT_LANG
    if not counts:
        # Latin-script (or letterless): try orthographic de/fr/es disambiguation
        # before falling back to the English default (I18N-1). Guarded so a fault
        # in the scorer never crashes the triage path.
        try:
            latin = _detect_latin(text)
        except Exception as exc:  # noqa: BLE001 — fail-safe to en
            log.setup.warning(
                "lang_detect.detect_language: latin scorer failed — defaulting to en",
                extra={"_fields": {"error": str(exc)}},
            )
            return _DEFAULT_LANG
        log.setup.debug(
            "lang_detect.detect_language: exit — latin path",
            extra={"_fields": {"detected": latin}},
        )
        return latin
    dominant = max(counts.items(), key=lambda kv: kv[1])[0]
    log.setup.debug(
        "lang_detect.detect_language: exit",
        extra={"_fields": {"detected": dominant, "counts": counts}},
    )
    return dominant
