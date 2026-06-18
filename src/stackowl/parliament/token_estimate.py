"""Multilingual token estimation for Parliament budget accounting (PARL-1 / F078).

No real subword tokenizer is wired into the provider layer, so we approximate
token count in a script-aware way rather than the naive ``len(text) // 4`` byte
heuristic (which grossly undercounts space-free scripts like Chinese/Japanese
and overcounts long Latin words). The estimate is intentionally conservative —
budget accounting only needs to be *directionally* honest, not exact — and it
contains NO hardcoded English: it works on Unicode general categories only.

Heuristic, per the Unicode code-point sequence:

* Each CJK/Hangul/Hiragana/Katakana ideograph counts as ~1 token (these scripts
  are written without spaces and subword tokenizers emit roughly one token per
  character).
* Each maximal run of Latin/Cyrillic/Greek/etc. word characters counts as
  ``ceil(len / 4)`` tokens (a typical BPE merges ~4 chars into a subword).
* Each non-whitespace symbol/punctuation code point counts as 1 token.
* Whitespace is free.
"""

from __future__ import annotations

import math
import unicodedata

# Unicode blocks that are conventionally written without word spacing, where a
# subword tokenizer emits roughly one token per character.
_CHARS_PER_LATIN_TOKEN = 4


def _is_spaceless_script(ch: str) -> bool:
    """True for CJK/Kana/Hangul code points (one-token-per-char scripts)."""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0x3400 <= code <= 0x4DBF  # CJK Extension A
        or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x3040 <= code <= 0x30FF  # Hiragana + Katakana
        or 0xAC00 <= code <= 0xD7AF  # Hangul Syllables
        or 0x20000 <= code <= 0x2FA1F  # CJK Extension B–F + supplement
    )


def estimate_tokens(text: str) -> int:
    """Estimate the token count of ``text`` in a script-aware, language-neutral way.

    Returns 0 for empty/whitespace-only input. Always ``>= 1`` for any text that
    contains at least one non-whitespace code point.
    """
    if not text:
        return 0
    tokens = 0
    word_run = 0  # length of the current Latin/Cyrillic/… word character run

    def flush_word() -> int:
        if word_run == 0:
            return 0
        return math.ceil(word_run / _CHARS_PER_LATIN_TOKEN)

    for ch in text:
        if ch.isspace():
            tokens += flush_word()
            word_run = 0
            continue
        if _is_spaceless_script(ch):
            tokens += flush_word()
            word_run = 0
            tokens += 1
            continue
        category = unicodedata.category(ch)
        if category[0] in ("L", "N", "M"):  # letter, number, combining mark
            word_run += 1
        else:  # symbol / punctuation — its own token
            tokens += flush_word()
            word_run = 0
            tokens += 1
    tokens += flush_word()
    return tokens
