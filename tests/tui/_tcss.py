"""One definition of "is this CSS token-pure", shared by every test that asks.

WHY THIS EXISTS. Four TUI tests scanned Textual CSS for literal colours, and the
primitives were copied rather than shared: `_HEX_RE` and `_RGB_RE` were defined
identically in THREE files and `_strip_comments` in THREE (an overlapping set of
four files in total).

The copies had already drifted in the way that matters. Two of them strip
``/* ... */`` before scanning and say why — "those legitimately can include hex
examples" — and `test_banner.py` carries the regexes WITHOUT the stripping. So a
commented-out colour example would be reported there as a violation, while the
same text passes in the other two. The requirement was documented in two copies
and simply absent from the third.

MEASURED 2026-09-06 and it is LATENT, not live: `Banner.DEFAULT_CSS` contains no
comments today, and `stackowl.tcss` has 5 comment blocks with 0 hex literals
inside them. Nothing fails right now. It is recorded and fixed here because the
next person to write a commented colour example is the one who pays, and because
"improve one copy, leave two behind" is how a guard silently weakens — this repo
has now paid for that shape five times.

WHY THE SCAN AND THE STRIP ARE ONE CALL, which is the actual fix and was not the
first attempt. Sharing the three primitives and leaving each caller to compose
them changed NOTHING: `test_banner.py` still scanned unstripped text, and a
mutant — a commented-out `#ff0000` in `Banner.DEFAULT_CSS` — failed identically
before and after. Deduplicating the pieces of a two-step sequence does not
deduplicate the SEQUENCE, and the sequence was the part being forgotten.

So `hex_literals` and `has_rgb_literal` strip internally, and the patterns
themselves are PRIVATE. Exporting them beside the safe helpers would have left
the unsafe composition available and equally convenient, which is how the third
copy came to differ in the first place. `strip_comments` stays exported for the
one caller that wants a stripped BODY rather than a verdict.
"""

from __future__ import annotations

import re

#: A literal hex colour. Tokens are what CSS here is allowed to reference.
_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")

#: A literal ``rgb()`` / ``rgba()`` colour.
_RGB_RE = re.compile(r"rgba?\s*\(")


def strip_comments(text: str) -> str:
    """Remove ``/* ... */`` blocks so commented examples do not trip a scanner.

    Textual CSS has no line-comment form, so block comments are the whole rule.
    Prefer `hex_literals` / `has_rgb_literal`, which strip for you — this is
    exported for callers that need the stripped body itself, not a verdict.
    """
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def hex_literals(css: str) -> list[str]:
    """Every literal hex colour in *css*, ignoring commented-out examples.

    The strip is not the caller's job. That is the whole point: it was the
    caller's job in three places and one of them forgot.
    """
    return _HEX_RE.findall(strip_comments(css))


def has_rgb_literal(css: str) -> bool:
    """Whether *css* contains a literal ``rgb()``/``rgba()``, comments ignored."""
    return _RGB_RE.search(strip_comments(css)) is not None
