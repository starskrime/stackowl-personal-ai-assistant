"""The corpus has always had two document genres; the report knew one.

`DOC_STANDARD` describes a blockquote header — `> **Last verified:** …`. TWENTY
design documents instead open with prose fields:

    **Item.** D17.4 · URL / path / egress guards — `PARITY`
    **Last verified.** 2026-09-05, commit `91f71750`

A PERIOD, not a colon, and no blockquote. `doc_check` reported "no dated `Last
verified` in the header" about every one of them, and every one carries a date.
**"Unmeasurable" was a claim about the CORPUS and a fact about the PARSER** — the
second time in this same instrument, and its own docstring records the first:
eleven documents were filed as headerless because `Source (new):` and src-relative
paths went unread.

THE NUMBER WAS ON THE BOARD EVERY LOOP AND NOBODY COULD ACT ON IT. "unmeasurable
36" was printed at the start of every invocation for days. It stayed untouched
because the stated reason was wrong: it invited someone to add a date that was
already there, and said nothing about what was actually missing.

MEASURED 2026-09-08, the honest three-way split of the same 36:

  16  dated, but declare no Source — staleness cannot be computed
  14  declare no verification date at all
   6  DECLINED — nothing was built, so nothing can go stale

The last bucket is the one that matters most, because it is NOT A GAP. A large
part of the prose genre is declined items — "The Answer To The Ask Is No",
"Nothing was built" — where the Ask was measured and refused. Such a document
cites no source because there is none: nothing was written, so nothing can drift,
and undatable is its correct and permanent state.

IT IS ASKED OF THE RECORD, NOT OF THE PROSE. The obvious alternative is to match
the document's own words, and this repo has watched four guards break on, or be
satisfied by, a comment. `stages.implement == no_change_needed` is the same fact
where it is already maintained.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"


def _doc_check():
    path = _ROOT / "scripts" / "doc_check.py"
    spec = importlib.util.spec_from_file_location("_doc_check_genres", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_doc_check_genres"] = mod
    spec.loader.exec_module(mod)
    return mod


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
#: The prose genre's own spelling, stated here ONLY to find the population — the
#: parser under test is asked for the value, never re-implemented.
_PROSE_VERIFIED = re.compile(r"^\*\*Last verified\.\*\*\s*(\d{4}-\d{2}-\d{2})", re.M)


@pytest.mark.tripwire
def test_a_prose_header_is_read_like_a_blockquote_one() -> None:
    """THE LIVE CASE. D17.4 dates itself and the report said it did not."""
    mod = _doc_check()
    head = mod._header((_DESIGNS / "D17.4.md").read_text(encoding="utf-8"))  # noqa: SLF001
    assert _DATE.search(head.get("Last verified", "")), (
        "the prose genre's `**Last verified.**` is still invisible to the parser, so "
        "the report goes on saying these documents carry no date"
    )


@pytest.mark.tripwire
def test_a_wrapped_prose_field_is_joined_back_together() -> None:
    """D10.3's `Last verified.` runs to FOUR lines.

    The blockquote parser learned this the hard way — 27 documents lost real source
    paths to unjoined continuations — and the prose genre wraps exactly the same
    way. A field read only to its first line-break loses whatever explains it.
    """
    mod = _doc_check()
    head = mod._header((_DESIGNS / "D10.3.md").read_text(encoding="utf-8"))  # noqa: SLF001
    value = head.get("Last verified", "")
    assert _DATE.search(value) and len(value) > 90, (
        f"the wrapped field was truncated at its first newline: {value!r}"
    )


@pytest.mark.tripwire
def test_no_document_that_declares_a_date_is_reported_as_undated() -> None:
    """THE PROPERTY, swept over the whole corpus rather than the two cases above.

    A guard pinned to D17.4 and D10.3 would pass while the other eighteen stayed
    invisible. This asks every document that declares a date in EITHER genre
    whether the parser can find one.
    """
    mod = _doc_check()
    blind: list[str] = []
    for doc in sorted(_DESIGNS.glob("*.md")):
        text = doc.read_text(encoding="utf-8")
        if not _PROSE_VERIFIED.search(text):
            continue
        head = mod._header(text)  # noqa: SLF001
        if not _DATE.search(head.get("Last verified", "")):
            blind.append(doc.name)
    assert not blind, (
        "these documents state a verification date the parser cannot see, so the "
        f"report calls them undated: {blind}"
    )


@pytest.mark.tripwire
def test_a_declined_item_is_not_reported_as_a_gap() -> None:
    """An item that BUILT NOTHING cites no source correctly.

    Reporting it beside the real gaps is how a list of 36 became a list nobody
    worked: a bucket you cannot act on teaches the reader to skip the bucket.
    """
    mod = _doc_check()
    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    declined = [
        str(e.get("id")) for e in (data.get("items") or [])
        if isinstance(e, dict)
        and (e.get("stages") or {}).get("implement") == "no_change_needed"
    ]
    assert declined, "no item records a declined implement — the split guards nothing"
    # At least one of them must be a document the report can classify that way.
    classified = [d for d in declined if mod._built_nothing(f"{d}.md")]  # noqa: SLF001
    assert classified, (
        "the report can no longer recognise a declined item, so every one of them "
        "is being listed as a missing Source somebody should go and add"
    )


@pytest.mark.tripwire
def test_the_prose_genre_is_still_a_real_population() -> None:
    """VACUITY CONTROL. Every assertion above passes over an empty corpus.

    If the genre were migrated away, or the finder stopped matching, this file
    would read green while guarding nothing — the shape this repo has now paid for
    in four instruments.
    """
    n = sum(
        1 for doc in _DESIGNS.glob("*.md")
        if _PROSE_VERIFIED.search(doc.read_text(encoding="utf-8"))
    )
    assert n >= 15, (
        f"only {n} documents use the prose genre, against 20 measured on 2026-09-08 — "
        f"either the corpus was migrated (delete this file with it) or the finder "
        f"no longer recognises the shape"
    )
