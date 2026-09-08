"""A document may not carry the same header field twice — the parser keeps one.

WHY THIS EXISTS, and it is a defect I nearly shipped rather than one I found.

`doc_check._header` builds its dict with a plain assignment — `out[last] = ...` —
so a second `> **Reviewed:**` in one document SILENTLY DISCARDS the first. Nothing
warns; the field simply loses a value.

`Reviewed:` is exactly the field that wants to accumulate. It dismisses ONE named
commit, and a document outlives many commits, so the second dismissal is inevitable.
MEASURED 2026-09-08 while draining seven documents `bf603ef7` had marked stale: my
first applier ADDED a second `Reviewed:` to four documents that already had one, and
`_reviewed_shas` then returned only `{bf603ef7}` for every one of them. `30eecd8e`
and `1f48d999` — commits already examined and dismissed — silently returned to the
stale list. The applier's own read-back caught it because it counted the fields;
had it counted the SHA instead it would have passed.

THE CORPUS IS CLEAN TODAY — 0 duplicates across 84 documents, measured before the
change and again after. So this guard ships green and can only ratchet: it does not
demand work, it forbids a specific silent loss.

WHY NOT FIX THE PARSER INSTEAD. Making `_header` accumulate would change what every
existing field means — `Source:` and `Last verified:` are single-valued by design,
and a reader who wrote one twice wants the LAST, not a concatenation. The defect is
not the assignment; it is that a duplicate is invisible. So the duplicate is what
gets forbidden.
"""

from __future__ import annotations

import collections
import pathlib
import re
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"

#: `> **Field:** value` — the blockquote header shape `_header` parses.
_FIELD = re.compile(r">\s*\*\*([A-Za-z ()]+):\*\*")


def _header_block(text: str) -> str:
    """Everything before the first `##`, which is where `_header` stops looking."""
    cut = text.find("\n## ")
    return text[:cut] if cut != -1 else text[:3000]


def _fields(path: pathlib.Path) -> collections.Counter[str]:
    return collections.Counter(_FIELD.findall(_header_block(path.read_text("utf-8"))))


@pytest.mark.tripwire
def test_no_document_declares_the_same_header_field_twice() -> None:
    """THE SILENT LOSS. `_header` assigns; the second occurrence wins and the first
    is gone with no warning."""
    offenders = [
        f"{p.name}: '{field}' appears {n} times"
        for p in sorted(_DESIGNS.glob("*.md"))
        for field, n in _fields(p).items()
        if n > 1
    ]
    assert not offenders, (
        "`doc_check._header` keeps only the LAST occurrence of a header field, so "
        "these documents have silently lost a value. For `Reviewed:` that means a "
        "commit already examined and dismissed returns to the stale list. Merge the "
        "values into ONE field rather than repeating it:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.tripwire
def test_the_scan_actually_sees_the_headers() -> None:
    """0 OVER 0 IS NOT A PASS. If the header shape changes, the assertion above
    passes by reading nothing — which is how a guard becomes decoration."""
    docs = sorted(_DESIGNS.glob("*.md"))
    assert len(docs) > 50, f"only {len(docs)} design documents found"

    # MEASURED 2026-09-08: 51 of 84 documents carry blockquote header fields, 309
    # fields in all. The other 33 use the `**Item.** / **Ask.**` prose genre that
    # `doc_check` reports separately and that has no header fields to duplicate.
    # The floors sit well below both and well above zero — I first wrote 60 from
    # memory and the test failed against a clean corpus, which is the guard crying
    # wolf on correct work before it had shipped.
    with_fields = [p for p in docs if _fields(p)]
    total = sum(sum(_fields(p).values()) for p in with_fields)
    assert len(with_fields) >= 40, (
        f"only {len(with_fields)} of {len(docs)} documents yielded any header field "
        f"(51 when this guard shipped); the parser here has stopped matching the "
        f"corpus and the duplicate check above is scanning nothing"
    )
    assert total >= 200, (
        f"only {total} header fields seen across {len(with_fields)} documents (309 "
        f"when this shipped) — the shape matched but the depth did not"
    )


def test_a_planted_duplicate_is_caught(tmp_path, monkeypatch) -> None:
    """POSITIVE CONTROL — the corpus is clean, so without this the guard is only
    ever asserting the absence of something it has never been shown detecting.

    It drives the ASSERTION, not just the counter. An earlier version of this
    control checked that `_fields` returned 2 and stopped there, which proves the
    helper counts and says nothing about whether the guard would fail. Verified
    against a real corpus too: planting a second `Reviewed:` into a copy of the
    live design set makes the assertion below fail with
    "D05.1.md: 'Reviewed' appears 2 times".
    """
    doc = tmp_path / "D99.9.md"
    doc.write_text(
        "# t\n\n"
        "> **Reviewed:** `aaaaaaa` — first\n"
        "> **Reviewed:** `bbbbbbb` — second\n"
        "> **Last verified:** 2026-01-01\n\n"
        "## Body\n",
        encoding="utf-8",
    )
    assert _fields(doc)["Reviewed"] == 2

    monkeypatch.setattr(sys.modules[__name__], "_DESIGNS", tmp_path)
    with pytest.raises(AssertionError, match="Reviewed"):
        test_no_document_declares_the_same_header_field_twice()


def test_the_parser_really_does_keep_only_the_last(tmp_path) -> None:
    """The premise, asserted against `doc_check` itself rather than restated.

    If `_header` is ever changed to accumulate, this fails and the guard above
    becomes unnecessary — which is the right way to find that out.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "_doc_check_dupe_probe", _ROOT / "scripts" / "doc_check.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_doc_check_dupe_probe"] = mod
    spec.loader.exec_module(mod)

    head = mod._header(  # noqa: SLF001
        "# t\n\n> **Reviewed:** `aaaaaaa` — first\n"
        "> **Reviewed:** `bbbbbbb` — second\n\n## Body\n"
    )
    assert "aaaaaaa" not in head.get("Reviewed", ""), (
        "`_header` now keeps both values; the duplicate guard can be retired"
    )
    assert "bbbbbbb" in head.get("Reviewed", "")
