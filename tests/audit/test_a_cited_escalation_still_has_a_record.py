"""An escalation a design document cites must still have an entry in the record.

MEASURED 2026-09-08: **35 escalation ids cited across the design corpus had no
entry anywhere in `progress.yml`** — not open, not resolved, nothing. A reader
following `ESC-35` from D05.8 found nothing at all, and could not tell whether the
question had been answered or forgotten.

THE CAUSE IS A DATA-STRUCTURE MIGRATION, and `escalation_check.py`'s own docstring
already named it without anyone measuring the blast radius: the queue "used to be
a LIST that was PRUNED on answer, and is now a DICT that RETAINS the entry with a
`resolution`". The restructure in `480571bc` (2026-08-30) carried over only what
was OPEN at that moment — so every already-answered escalation lost its record,
**and with it the operator's ANSWER.**

WHAT THAT COST, concretely. ESC-35 was decided on 2026-08-23: *"WINDOW-RELATIVE,
BUT ONLY AFTER ESC-36. Demote tool_count_cap to an opt-out ceiling and size the
presented set against the resolved model window."* Its prerequisite ESC-36 was
implemented and validated live the SAME DAY. D05.8 has since described ESC-35 as
an undecided question gating two of its invariants, and `tool_count_cap: 150` is
still a raw count. An answered decision sat unbuilt for sixteen days because the
sentence recording it had been deleted.

22 of the 35 were recovered verbatim from `480571bc^` and restored with their
provenance. THIRTEEN REMAIN UNRECOVERED — 1, 4, 7, 8, 9, 10, 13, 14, 16, 58, 59,
61, 71 — because they predate that commit's parent too. That is why this is a
RATCHET and not a gate: failing on thirteen historical losses would fail every
unrelated change until someone excavated deeper history, which is how a gate gets
bypassed rather than satisfied. The number can only fall.

THE PROPERTY IS TENSE-FREE, DELIBERATELY. An earlier detector in
`escalation_check.py` asks whether a citation describes an escalation as open
RIGHT NOW, and it missed this entirely: D05.8's sentence wraps, with `(ESC-35)` on
line 464 and "stays open" on line 466, and that detector reads one line at a time.
Trying to widen it to paragraph scope was MEASURED and rejected — it added ten
sites of which seven were noise (a docstring about the ESCALATE sentinel, a schema
listing, a test fixture). Asking instead whether the RECORD STILL HOLDS THE ID
needs no prose reading at all, so it cannot be fooled by wrapping or by tense.
"""

from __future__ import annotations

import collections
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"

_REF = re.compile(r"ESC[-_](\d+)")
#: An entry OF ITS OWN, anywhere in the record — `ESC_<n>_<slug>:` as a mapping key.
#: A mention inside someone else's prose is a citation, not a record.
_ENTRY = re.compile(r"^\s*ESC_(\d+)[A-Za-z0-9_]*:", re.M)

#: The population on the day the rule was written, AFTER 22 were restored. A ceiling,
#: never a target.
_UNRECOVERED_2026_09_08 = 13


def _cited() -> dict[int, set[str]]:
    out: dict[int, set[str]] = collections.defaultdict(set)
    for doc in sorted(_DESIGNS.glob("*.md")):
        for m in _REF.finditer(doc.read_text(encoding="utf-8")):
            out[int(m.group(1))].add(doc.name)
    return out


def _has_an_entry() -> set[int]:
    record = (_ROOT / "progress.yml").read_text(encoding="utf-8")
    return {int(m.group(1)) for m in _ENTRY.finditer(record)}


@pytest.mark.tripwire
def test_no_new_citation_points_at_a_missing_record() -> None:
    cited, entries = _cited(), _has_an_entry()
    orphaned = sorted(n for n in cited if n not in entries)
    assert len(orphaned) <= _UNRECOVERED_2026_09_08, (
        f"{len(orphaned)} escalation ids are cited by a design document with no entry "
        f"anywhere in progress.yml, against {_UNRECOVERED_2026_09_08} historical losses "
        f"on 2026-09-08. A citation pointing at nothing cannot tell a reader whether the "
        f"question was answered or forgotten — and an answer that vanishes is an "
        f"instruction from the operator that stops being followed. Orphaned: {orphaned}"
    )


@pytest.mark.tripwire
def test_the_walk_sees_both_populations() -> None:
    """VACUITY CONTROL, and both halves matter.

    A glob that matched no documents, or an `_ENTRY` regex that matched every
    mention rather than only keys, would each make the assertion above pass while
    checking nothing.
    """
    cited, entries = _cited(), _has_an_entry()
    assert len(cited) >= 50, f"only {len(cited)} ids cited across the design corpus"
    assert len(entries) >= 90, f"only {len(entries)} ids hold an entry in the record"


@pytest.mark.tripwire
def test_a_recovered_entry_says_where_it_came_from() -> None:
    """A restored record that does not say it was restored is indistinguishable from
    one that was there all along — and the next reader cannot audit the recovery."""
    import yaml

    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    esc = (data.get("current") or {}).get("ESCALATIONS") or {}
    recovered = {k: v for k, v in esc.items()
                 if isinstance(v, dict) and "recovered_from" in v}
    # ASK HISTORY, DO NOT PIN A NUMBER. The first version of this asserted
    # `len(recovered) >= 22` — and 22 was itself the defect: the recovery indexed
    # `entries[id][0]`, restoring ONE entry per id over a population of THIRTY-NINE,
    # so it kept the implementation notes and dropped the operator's actual answers
    # (`ESC_35_tool_count_cap`, `ESC_36_tool_ordering`, `ESC_44_skill_ordering` …).
    # The guard passed, because it counted the same wrong population the recovery
    # had. A per-ID walk over a per-ENTRY set — the denominator error, in the code
    # written to cure a record loss, one loop after shipping a guard about
    # denominators.
    #
    # So the assertion is now an EQUALITY against the source: for every id we claim
    # to have recovered, every `ESC_*` key that commit's parent holds must be here.
    # That number cannot be chosen wrongly because it is not chosen at all.
    # THE SCOPE IS PINNED, NOT DERIVED FROM THE SET UNDER TEST. Deriving it from
    # `recovered` is circular and a mutation proved it: strip one entry's
    # `recovered_from` and its id leaves the scope with it, so the expectation
    # shrinks to match the damage and the guard passes. These 22 ids are a fact
    # about what was recovered on 2026-09-08, not a threshold — a later recovery
    # widens the list deliberately, in a diff someone reads.
    ids = {21, 22, 23, 24, 25, 26, 27, 28, 29, 31, 35, 36, 37, 38, 41, 43, 44, 46,
           47, 49, 52, 54}
    history = subprocess.run(
        ["git", "show", "480571bc^:progress.yml"],
        capture_output=True, text=True, timeout=120, cwd=_ROOT,
    ).stdout
    assert history, "could not read the pre-restructure record; the recovery is unauditable"
    expected = {
        m.group(1) for m in re.finditer(r"^\s*(ESC_(\d+)[A-Za-z0-9_]*):", history, re.M)
        if int(m.group(2)) in ids
    }
    dropped = sorted(expected - set(recovered))
    assert not dropped, (
        f"{len(dropped)} entries that history holds for the recovered ids are missing "
        f"from the record. A recovery that keeps one entry per id silently discards the "
        f"rest — and the ones it discarded were the ANSWERS: {dropped}"
    )
    for key, body in recovered.items():
        assert "480571bc" in str(body["recovered_from"]), (
            f"{key} does not name the commit it was recovered from, so the restoration "
            f"cannot be audited against history"
        )
        assert body.get("resolution"), (
            f"{key} has no `resolution`, so `escalation_check` will count a settled "
            f"question as an open one and hand it back to the operator"
        )
