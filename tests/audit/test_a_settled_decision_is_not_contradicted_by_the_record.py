"""A decision the operator settled must not be re-asserted the other way elsewhere.

WHY THIS EXISTS. ESC-149 (operator, 2026-09-05) settled that automatic prefix caching is
a property of a BACKEND, not of a wire protocol, and that this deployment cannot observe
it — so the frozen prompt must be treated as a real recurring cost. DEBT-122 folded that
correction into `providers/base.py`, which now carries it in full.

IT LANDED IN ONE OF THREE PLACES. MEASURED 2026-09-06, D01.2 still opened with "On an
OpenAI-protocol backend that is sufficient — prefix caching there is automatic, and the
discount arrives with no further work", and `pipeline/steps/assemble.py` still justified
two live design decisions with the same retracted premise: the always-present skill
catalogue ("a block that vanishes on some turns forfeits the cache on every turn, which
costs more than the tokens it saves") and moving memory out of the prompt.

So the record contradicted itself on a question the operator had explicitly answered, and
nothing could see it. That is the shape this codebase names as defect 3 — two copies of
one rule — with the twist that the copies disagreed, and the stale one was the one a
reader meets first, in the document.

THE MEASUREMENT BEHIND THE RETRACTION, so this file states what it is defending:
`cached_input_tokens` is 0 across 130,983 calls and 710,857,506 input tokens;
`cache_stats_reported` reads `not_reported` on 5,977 of 5,977 readings; the frozen prefix
is p50 13,985 chars (~3,632 tokens) on every call, ~10.4M tokens re-sent since 08-31.

WHAT THIS GUARD IS AND IS NOT. It does not try to detect "a document contradicting a
decision" in general — that needs to read meaning, and a guard that cries wolf on correct
work is the failure this programme keeps paying for. It pins the three sites where the
retraction actually lives, so reverting one is loud. The general gap — an ANSWERED
escalation has no `decision_check` the way an open one has a `premise_check` — is
recorded in the item, not built here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: The retracted claim, in the wording it kept reappearing in.
_RETRACTED = re.compile(
    r"prefix caching (there )?is automatic"
    r"|caches a byte-identical prefix AUTOMATICALLY",
    re.I,
)

#: Every site that must carry the retraction, and the marker proving it does.
_SITES = {
    "src/stackowl/providers/base.py": "ESC-149",
    "docs/reference-mapping/designs/D01.2.md": "CORRECTED 2026-09-06",
    "src/stackowl/pipeline/steps/assemble.py": "ESC-149",
}


def _text(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


class TestTheRetractionIsStillInPlace:
    @pytest.mark.tripwire
    def test_every_site_that_carried_the_claim_still_carries_its_retraction(self) -> None:
        missing = {
            rel: f"no {marker!r} — the retraction was removed or the file was rewritten"
            for rel, marker in _SITES.items()
            if marker not in _text(rel)
        }

        assert not missing, (
            "a decision the operator settled in ESC-149 is no longer recorded at a site "
            f"that used to assert the opposite: {missing}"
        )

    @pytest.mark.tripwire
    def test_no_site_states_the_retracted_claim_without_retracting_it(self) -> None:
        """The claim may APPEAR — all three sites quote it in order to correct it. What
        must never happen is an appearance with no retraction anywhere in the file."""
        offenders = {}
        for rel, marker in _SITES.items():
            text = _text(rel)
            if _RETRACTED.search(text) and marker not in text:
                offenders[rel] = "states the claim with nothing retracting it"

        assert not offenders, offenders

    def test_the_guard_sees_the_real_files(self) -> None:
        """VACUITY CONTROL. Every assertion above passes trivially if the files moved
        and the reads returned something empty."""
        for rel in _SITES:
            assert len(_text(rel)) > 500, f"{rel} is suspiciously small"

    def test_the_claim_really_is_present_to_be_retracted(self) -> None:
        """The other direction: if no site quotes the claim any more, the pins above
        guard a shape nothing uses and should be deleted rather than kept as scenery."""
        quoting = [rel for rel in _SITES if _RETRACTED.search(_text(rel))]

        assert quoting, (
            "no site quotes the retracted claim any more — this guard is now dead "
            "weight and 'retired means deleted' applies to it"
        )


class TestTheClaimHasNotSpreadSomewhereNew:
    """The sweep that found the three sites, kept runnable so a FOURTH is caught."""

    @pytest.mark.tripwire
    def test_the_retracted_claim_appears_only_where_it_is_being_corrected(self) -> None:
        strays: dict[str, str] = {}
        for root in ("src", "docs", "scripts"):
            for path in (_ROOT / root).rglob("*"):
                if path.suffix not in (".py", ".md") or not path.is_file():
                    continue
                rel = str(path.relative_to(_ROOT))
                if rel in _SITES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                match = _RETRACTED.search(text)
                # D01.1 narrates the same retraction about its own paragraph; any file
                # that names ESC-149 or marks a correction is discussing it, not
                # asserting it.
                if match and not re.search(r"ESC-149|used to\s+> ?assert|CORRECTED", text):
                    line = text[: match.start()].count("\n") + 1
                    strays[f"{rel}:{line}"] = match.group(0)

        assert not strays, (
            "the retracted ESC-149 claim is asserted somewhere new, with nothing "
            f"correcting it: {strays}"
        )
