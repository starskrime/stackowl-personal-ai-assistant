"""A design document may state a config default. It may not state it ALONE.

WHY THIS EXISTS. `doc_check.py` dates a document against the files it names as a
`Source:`. That is the right question for prose, and it cannot see the most
copy-able fact a document carries: the DEFAULT VALUE of a config key. MEASURED
2026-09-08 — sixteen such rows are published across the design set, and
`config/settings.py`, which defines every one of them, is cited as a `Source:`
only by the documents that happen to be ABOUT settings.

So the defect is structural: a default changes in the tree, and every document
restating it stays "fresh" by every instrument this programme owns.

IT WAS FOUND BY COMMITTING IT. DEBT-238 moved `orchestrator.tool_count_cap` from
40 to `HARD_TOOL_COUNT_CAP` (150). D05.8 cites `settings.py`, so `doc_check`
reported it STALE and it was corrected. D05.4 publishes the SAME default in its
Configuration table, cites four other files, and was reported CLEAN while it said
40 — a document made actively false by a commit, by an instrument built to catch
exactly that, silent because the falsifying file was one the document documents
but does not watch.

The cure is this programme's own, for the fourth time: ONE SOURCE, ASKED —
`premise_check` for escalations, `closing_check` for partial stages, `doc_check`
for prose staleness, and now the published default asked of the code that defines
it. `doc_check` REPORTS it; this file is the gate, because that is where every
other cross-cutting guard in this tree lives.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"

#: 16 config rows were published on the day this guard shipped. The floor is set
#: at HALF that, deliberately, and the gap is the point.
#:
#: A floor detects ONE thing — the parser has stopped seeing the corpus, which
#: would make every other assertion here pass vacuously. It must not also be a
#: pin on the corpus's size. Set at 16 it would fail the day a setting is
#: RETIRED and its documentation correctly deleted with it, which is this
#: programme's own rule; a guard that cries wolf on correct work is the failure
#: it keeps paying for, and `test_a_doc_does_not_pin_a_count_that_grows_by_design`
#: exists because four documents already froze a number this way.
#:
#: 8 is far below any legitimate drift and far above the 0 that every
#: parser-blindness mutation produces, which is the only range that matters.
_MINIMUM_ROWS = 8


def _doc_check():
    """Import `scripts/doc_check.py` and ASK it, rather than restating its regexes.

    A second copy of the parser here would be a second copy of one rule — the
    shape this whole guard exists to prevent.
    """
    path = _ROOT / "scripts" / "doc_check.py"
    spec = importlib.util.spec_from_file_location("_doc_check_config_probe", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_doc_check_config_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def claims():
    from stackowl.config.settings import Settings

    return _doc_check().config_claims(Settings(), sorted(_DESIGNS.glob("*.md")))


@pytest.mark.tripwire
def test_no_document_publishes_a_default_the_code_does_not_have(claims) -> None:
    """THE GATE. D05.4 said 40 for the whole of the day DEBT-238 shipped."""
    wrong = claims["false"]
    assert not wrong, "\n".join(
        [f"{len(wrong)} published default(s) disagree with `config/settings.py`:"]
        + [
            f"  {c.doc}:{c.line}  {c.key} — the document says {c.claimed!r}, "
            f"the code says {c.live!r}"
            for c in wrong
        ]
    )


@pytest.mark.tripwire
def test_no_document_documents_a_setting_that_is_gone(claims) -> None:
    """The RETIREMENT half. Eleven of nineteen stale documents in the 2026-09-07
    census were made stale by a commit that SUBTRACTED something, and a date can
    never say REMOVED — only that a source moved."""
    gone = claims["gone"]
    assert not gone, "\n".join(
        [f"{len(gone)} document(s) document a config field its section no longer has:"]
        + [f"  {c.doc}:{c.line}  {c.key}" for c in gone]
    )


@pytest.mark.tripwire
def test_the_checker_is_actually_reading_the_corpus(claims) -> None:
    """0 FALSE over 0 ROWS IS NOT A PASS.

    Every assertion above is satisfied by a parser that matches nothing, and that
    is not a hypothetical failure mode here: two earlier drafts of this parser
    were wrong about the corpus in opposite directions, and the *quiet* one would
    have shipped looking exactly like success.
    """
    total = sum(len(v) for v in claims.values())
    assert total >= _MINIMUM_ROWS, (
        f"only {total} config row(s) found across {len(list(_DESIGNS.glob('*.md')))} "
        f"design documents (16 when this guard shipped, floor {_MINIMUM_ROWS}) — the "
        f"parser has stopped seeing the corpus, and every other assertion in this "
        f"file passes vacuously while it does"
    )
    assert len(claims["ok"]) >= _MINIMUM_ROWS // 2, (
        f"{len(claims['ok'])} rows AGREE with the code; the guard proves nothing "
        f"unless it is successfully resolving real keys against real values"
    )


def test_a_disagreeing_row_is_detected(tmp_path) -> None:
    """POSITIVE CONTROL. A checker that finds nothing and a clean corpus are the
    same output, so the detector must be shown catching a planted defect."""
    from stackowl.config.settings import Settings

    doc = tmp_path / "D99.9.md"
    doc.write_text(
        "| Key | Type | Default | Notes |\n"
        "|---|---|---|---|\n"
        "| `orchestrator.tool_count_cap` | int | 40 | plainly false |\n",
        encoding="utf-8",
    )
    res = _doc_check().config_claims(Settings(), [doc])
    assert len(res["false"]) == 1, res
    assert res["false"][0].claimed == "40"
    assert res["false"][0].line == 3


def test_a_removed_setting_is_detected(tmp_path) -> None:
    """POSITIVE CONTROL FOR THE RETIREMENT HALF — added because MUTATION FOUND IT.

    `test_no_document_documents_a_setting_that_is_gone` passed against a build
    that had the GONE branch deleted outright, because no document in the corpus
    currently names a removed field: the assertion was 0 over 0, which this
    programme's own rule says is not a pass. The FALSE half had a planted control
    from the start and the GONE half did not, so only mutation could see it.

    `orchestrator` is a real section; `tool_count_cap_ceiling` was never a field
    in it — the shape a document takes on the day a setting is deleted from the
    tree and its documentation is not.
    """
    from stackowl.config.settings import Settings

    doc = tmp_path / "D99.5.md"
    doc.write_text(
        "| Key | Type | Default | Notes |\n"
        "|---|---|---|---|\n"
        "| `orchestrator.tool_count_cap_ceiling` | int | 40 | retired |\n",
        encoding="utf-8",
    )
    res = _doc_check().config_claims(Settings(), [doc])
    assert len(res["gone"]) == 1, res
    assert res["gone"][0].key == "orchestrator.tool_count_cap_ceiling"
    assert not res["false"] and not res["ok"], (
        "a field that does not exist was compared against a value anyway"
    )


def test_the_default_column_is_found_by_its_HEADER_not_its_position(tmp_path) -> None:
    """THE BUG THE SECOND DRAFT SHIPPED.

    Column three is the *Source* in one design table and the *Notes* in another.
    Reading by position produced 12 FALSE rows of which 10 were the instrument
    quoting the wrong column back at itself — "says 'stays'", "says 'Telegram'".
    Here the true default sits in the LAST column and a decoy `40` sits third.
    """
    from stackowl.config.settings import Settings

    doc = tmp_path / "D99.8.md"
    doc.write_text(
        "| Key | Applies to | Was | Default |\n"
        "|---|---|---|---|\n"
        "| `orchestrator.tool_count_cap` | both paths | 40 | 150 |\n",
        encoding="utf-8",
    )
    res = _doc_check().config_claims(Settings(), [doc])
    assert not res["false"], (
        f"the decoy 40 in column three was read as the default: {res['false']}"
    )
    assert len(res["ok"]) == 1 and res["ok"][0].claimed == "150"


def test_a_table_about_code_is_not_a_config_claim(tmp_path) -> None:
    """THE BUG THE FIRST DRAFT SHIPPED — seven 'deleted settings' that were never
    settings. Ordinary tables about code look exactly like config tables."""
    from stackowl.config.settings import Settings

    doc = tmp_path / "D99.7.md"
    doc.write_text(
        "| Name | Value | Meaning |\n"
        "|---|---|---|\n"
        "| `cache_audit._tools_hashes` | between turns | not a setting |\n"
        "| `gmail.py` | — | not a setting either |\n",
        encoding="utf-8",
    )
    res = _doc_check().config_claims(Settings(), [doc])
    assert not any(res.values()), f"a table about code was read as config: {res}"


def test_a_table_with_no_default_column_is_skipped(tmp_path) -> None:
    """A document may mention a setting without publishing its value. That is not
    a claim, and treating it as one is how a guard becomes noise."""
    from stackowl.config.settings import Settings

    doc = tmp_path / "D99.6.md"
    doc.write_text(
        "| Key | Notes |\n"
        "|---|---|\n"
        "| `orchestrator.tool_count_cap` | discussed at length, no value stated |\n",
        encoding="utf-8",
    )
    res = _doc_check().config_claims(Settings(), [doc])
    assert not any(res.values()), f"a table stating no value was read as a claim: {res}"
