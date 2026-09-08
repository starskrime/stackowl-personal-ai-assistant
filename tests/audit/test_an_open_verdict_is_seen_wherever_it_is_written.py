"""An OPEN acceptance check must be found wherever the document writes it.

MEASURED 2026-09-08. D09.2 said its live check was OPEN in two places — once in
its header, `> The live firing is OPEN — no conversation has rolled over since.`,
and once inside its Verification fence, `# live (OPEN — no rollover since the
restart)`. Neither starts a line and neither is bolded, and `_OPEN_CHECK` required
one or the other. So the report that exists to find exactly this could not see it.

IT WAS OPEN FOR NINE DAYS WITH ITS EVIDENCE ALREADY IN THE LOGS. The line the
document itself greps for — `rollover_summary.correction: standing instruction
recorded` — had fired **17 times**: 4 on 2026-08-31, 11 on 09-01, one on 09-02 and
one on 09-08. Every hit AFTER the document was written, so not one is
satisfied-by-history, and the emitter is reached only when a rollover produced a
correction and an owl was resolved — each hit is the feature working end to end.

THE ORIGINAL ANCHORING WAS A DELIBERATE GUARD, and its own comment said so: bold
or line-start "so a breaker described as OPEN in prose is not swept in". That
reasoning is sound and is kept. What changed is the discriminator: `OPEN` followed
by a DASH is a VERDICT AND ITS REASON. Prose about a breaker reads "the breaker is
open" or "opened"; it does not read "OPEN — because …".

AND THE LOOSENING WAS MEASURED BEFORE IT WAS BELIEVED, exactly as that decision
demanded. Against the whole corpus the widening adds TWO lines, both in D09.2,
both genuine, and no others. Zero false positives is what earns it.

THIS IS THE SAME SHAPE AS DEBT-234, one detector over: an instrument anchored to
the form it first saw, blind to a document that writes the same fact differently.
There the cost was a false "undated" on twenty documents; here it was an open
question nobody could re-ask.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"


def _doc_check():
    path = _ROOT / "scripts" / "doc_check.py"
    spec = importlib.util.spec_from_file_location("_doc_check_open", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_doc_check_open"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.tripwire
def test_an_OPEN_verdict_mid_line_is_found() -> None:
    """D09.2's exact two shapes, kept as fixtures because the live ones are CLOSED.

    Pinning the document itself would have made this test die the moment the
    thing it guards was fixed — which is precisely when the guard starts
    mattering, because the next document to write it that way has nobody
    watching.
    """
    mod = _doc_check()
    for line in (
        "> The live firing is OPEN — no conversation has rolled over since.",
        "# live (OPEN — no rollover since the restart)",
        "the cap is OPEN - nothing has presented one yet",
    ):
        assert mod._OPEN_CHECK.search(line), (  # noqa: SLF001
            f"an OPEN verdict written mid-line is invisible again: {line!r}"
        )


@pytest.mark.tripwire
def test_prose_that_merely_says_open_is_still_not_a_verdict() -> None:
    """THE GUARD THE ORIGINAL DECISION BUILT, and it is kept intact.

    A widening that swept in every sentence containing "open" would cry wolf on
    correct work — the failure this repo pays for most — and the OPEN report
    would become another list nobody reads.
    """
    mod = _doc_check()
    for line in (
        "the tool breaker is open and will close after the cooldown",
        "three breakers opened after the fix shipped",
        "an open question for the operator, queued as ESC-160",
        "we open the database read-only for this query",
    ):
        assert not mod._OPEN_CHECK.search(line), (  # noqa: SLF001
            f"prose about something being open was swept in as a verdict: {line!r}"
        )


@pytest.mark.tripwire
def test_the_detector_still_finds_a_real_population() -> None:
    """VACUITY CONTROL. Both assertions above pass against a regex that matches
    nothing in the actual corpus, so the corpus is asked too."""
    mod = _doc_check()
    found = sum(
        len(mod._open_acceptance_lines(doc.read_text(encoding="utf-8")))  # noqa: SLF001
        for doc in _DESIGNS.glob("*.md")
    )
    assert found >= 2, (
        f"the walk found {found} OPEN acceptance lines across the corpus. Either "
        f"every open check was genuinely closed — check that before believing it — "
        f"or the detector no longer recognises the shape."
    )
