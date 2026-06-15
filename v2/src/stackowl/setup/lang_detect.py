"""lang_detect — coarse, stdlib-only script-based language tag detection (F089/F098).

A pure ``unicodedata`` script classifier: it counts the Unicode script block of the
LETTER code points in a string and returns a coarse BCP-47-ish tag for the dominant
NON-Latin script (``ru`` Cyrillic, ``zh`` Han, ``ja`` Hiragana/Katakana, ``ar``
Arabic, ``el`` Greek, ``he`` Hebrew, ``hi`` Devanagari, ``ko`` Hangul). Latin (and
anything with no detectable letters) returns ``"en"``.

DELIBERATE COARSENESS (documented cut, per the C8 spec): a script classifier cannot
distinguish de/fr/es (all Latin) → those degrade to ``"en"`` honestly. That is
acceptable for a rare provider-down failure-path string; fine-grained Latin
disambiguation is the live LLM cascade's job and a lingua-py follow-up. NO hardcoded
English keyword list (hard mandate) — this is script-based only.

Never raises (fail-safe ``"en"``): this runs on the hot triage path before every
turn and must never crash it. No third-party dependency (ARM64/Jetson-safe).
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
        log.setup.debug("lang_detect.detect_language: exit — no non-Latin script, en")
        return _DEFAULT_LANG
    dominant = max(counts.items(), key=lambda kv: kv[1])[0]
    log.setup.debug(
        "lang_detect.detect_language: exit",
        extra={"_fields": {"detected": dominant, "counts": counts}},
    )
    return dominant
