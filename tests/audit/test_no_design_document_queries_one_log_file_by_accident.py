"""A Verification command that reads `stackowl.jsonl` sees only TODAY.

The retained window is eleven dated files plus the live one. A query pinned to
`stackowl.jsonl` alone is blind to every previous day, so it returns 0 for
anything older — and 0 reads as *not yet*, never as *wrong instrument*. That
mistake has cost this programme repeatedly; D04.5's check contradicted its own
document across five sections because of it.

MEASURED 2026-09-08: 31 such commands in 19 documents. All 31 were read in
context and they split, which is the finding:

* **24 were blind by accident** and now glob. Where the command takes a record
  by POSITION (`| tail -1`) the widening also added `| sort`, because a
  multi-file grep in this harness emits results as workers finish. Demonstrated
  live on `tier ladder resolved`: three identical unsorted runs returned
  2026-09-06, 2026-09-05 and 2026-09-06, while the true newest record was
  2026-09-08. Widening WITHOUT sorting would have traded a blind query for a
  non-deterministic one.
* **7 were deliberately scoped to the current boot and are CORRECT as written.**
  Three sit directly under `./start.sh && sleep 90` asking "did THIS boot log an
  ERROR"; one records "ZERO turns have run since the restart"; two ask "does the
  arithmetic reconcile on the current boot?", where globbing would sum eleven
  days of skill registrations against a one-boot denominator and quietly break a
  working check.

So the report's own number was wrong — the honest population was 24, not 31 —
and a future loop acting on "31" would have "fixed" seven correct queries. Those
seven now carry an explicit `# doc_check: current-boot` marker: a STRUCTURED
TOKEN rather than a prose whitelist, because "does a nearby comment say current
boot" is a guess at future phrasing, which is the failure mode this repo pays
for most.

WHY THIS ONE IS A GATE WHEN `doc_check` AS A WHOLE IS NOT. The staleness report
cannot be a tripwire — 15 of 33 measurable documents are stale on any given day,
so it would fail every unrelated change until someone re-read fifteen documents,
which is how a gate gets bypassed rather than satisfied. This property is
different in kind: the population is ZERO and stays zero unless somebody writes a
NEW blind query. It can only fire on the change that causes it, so it cries wolf
on nothing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"


def _doc_check():
    """Import `scripts/doc_check.py` and ASK IT, rather than restating its regex.

    Two copies of "what counts as a single-file log query" is exactly the
    two-copies-of-one-rule shape; if the detector's definition changes, this gate
    must move with it or it starts guarding a different property than the report
    describes.
    """
    path = _ROOT / "scripts" / "doc_check.py"
    spec = importlib.util.spec_from_file_location("_doc_check_for_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_doc_check_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.tripwire
def test_every_log_query_globs_or_declares_it_reads_one_boot() -> None:
    mod = _doc_check()
    offenders: list[str] = []
    for doc in sorted(_DESIGNS.glob("*.md")):
        text = doc.read_text(encoding="utf-8")
        for ln, line in mod._single_log_queries(text):  # noqa: SLF001
            offenders.append(f"{doc.name}:{ln}  {line[:90]}")

    assert not offenders, (
        "these Verification commands read only today's log, so they return 0 for "
        "anything older and that 0 reads as 'not yet'. Use "
        "`~/.stackowl/logs/stackowl*.jsonl` — adding `| sort` before any `tail` "
        "and `-a` to any grep — or, if the query means ONE boot on purpose, mark "
        f"it `# {mod._ON_PURPOSE}`:\n  " + "\n  ".join(offenders)  # noqa: SLF001
    )


@pytest.mark.tripwire
def test_the_marker_is_not_a_way_to_silence_the_gate() -> None:
    """An opt-out with no ceiling is an opt-out that eats the rule.

    The marker exists for a measured population of SEVEN. Pinning the count means
    an eighth has to be argued for in a diff rather than appearing quietly — the
    number is the point, not the mechanism.
    """
    mod = _doc_check()
    marked = sum(
        mod._on_purpose_queries(doc.read_text(encoding="utf-8"))  # noqa: SLF001
        for doc in sorted(_DESIGNS.glob("*.md"))
    )
    assert marked == 7, (
        f"{marked} queries are marked `{mod._ON_PURPOSE}`, not the 7 that were "  # noqa: SLF001
        f"read in context and justified on 2026-09-08. A new one may well be "
        f"correct — but it must be READ, not assumed, and then this number moved."
    )
