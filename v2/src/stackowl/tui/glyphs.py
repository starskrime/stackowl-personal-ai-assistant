"""BMP glyph registry with ASCII fallbacks for non-Unicode terminals."""

from __future__ import annotations

import os
from dataclasses import dataclass


class TerminalCapabilities:
    """Probes the environment for Unicode block rendering support."""

    @staticmethod
    def supports_unicode_blocks() -> bool:
        lang = os.environ.get("LANG", "")
        lc_all = os.environ.get("LC_ALL", "")
        term = os.environ.get("TERM", "")
        if "UTF-8" in lc_all.upper() or "UTF-8" in lang.upper():
            return True
        return "xterm" in term or "256color" in term


@dataclass(frozen=True)
class Glyph:
    """A character with a guaranteed-renderable ASCII fallback.

    Attributes:
        char: BMP character (codepoint <= 0xFFFF) used when the terminal supports it.
        fallback: ASCII-safe representation for non-Unicode terminals.
    """

    char: str
    fallback: str

    def __str__(self) -> str:
        if os.environ.get("STACKOWL_NO_GLYPHS") == "1":
            return self.fallback
        if TerminalCapabilities.supports_unicode_blocks():
            return self.char
        return self.fallback


# Standard glyphs used across TUI surfaces.
GLYPH_STEP_COMPLETE = Glyph("●", "*")  # BLACK CIRCLE
GLYPH_STEP_EMPTY = Glyph("○", "o")  # WHITE CIRCLE
GLYPH_SEPARATOR = Glyph("◆", "<>")  # BLACK DIAMOND
GLYPH_PARLIAMENT = Glyph("◈", "<P>")  # WHITE DIAMOND CONTAINING BLACK SMALL DIAMOND
GLYPH_PUSHBACK = Glyph("◈", "<!")  # WHITE DIAMOND CONTAINING BLACK SMALL DIAMOND
GLYPH_PROMPT = Glyph("❯", ">")  # HEAVY RIGHT-POINTING ANGLE QUOTATION MARK ORNAMENT
GLYPH_DNA_BARS = [Glyph(c, str(i)) for i, c in enumerate("▁▂▃▄▅▆▇█")]
