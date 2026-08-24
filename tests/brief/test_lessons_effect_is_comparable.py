"""The brief must not report a metric that is selected on the outcome.

WHAT BAKIR RECEIVED at 13:00 on 2026-08-24 — his first brief in 14 days, after the
seed repair finally delivered one:

    lessons_effect[interactive] injected:0.47(n=389) held_out:0.55(n=38)
    lessons_effect[machine]     injected:0.75(n=1351) held_out:0.89(n=205)

Read plainly that says withholding lessons produces better work. It is an
artifact.

THE MECHANISM. The critic scorer selects
``WHERE quality_score IS NULL AND success = 1 AND failure_class IS NULL``, so
ONLY SUCCESSFUL TURNS ARE EVER SCORED. This assembler then averages
quality_score ``WHERE quality_score IS NOT NULL`` — i.e. over survivors of that
gate. Conditioning on success, which both the treatment and the turn's quality
cause, induces a spurious negative association among survivors: if lessons rescue
marginal turns that would otherwise have failed, those turns join the INJECTED
scored pool and pull its mean down, while the held-out pool keeps only turns good
enough to succeed unaided.

MEASURED 2026-08-24 over 6,890 arm-carrying rows. On the metric that is NOT
conditioned on the gate — success itself — the sign is the other way:

    success   held_out 243/1016 (23.9%)   injected 1737/5874 (29.6%)   z = -3.68

So the brief reported the one comparison that cannot be made across arms, and
omitted the one that can.

THE FIX IS ADDITIVE. The quality line stays — it is real information about scored
turns — but it is labelled as success-gated, and a success-rate line sits beside
it. Removing the quality line would trade one incomplete picture for another; the
defect was never that the number existed, it was that nothing said what it was
conditioned on.
"""

from __future__ import annotations

import re

SRC = "src/stackowl/brief/assemblers.py"


def _source() -> str:
    with open(SRC, encoding="utf-8") as fh:
        return fh.read()


def test_a_success_rate_line_is_emitted() -> None:
    """The comparable metric must appear at all."""
    assert "lessons_success[" in _source(), (
        "the brief must report the arm comparison on SUCCESS, which is not "
        "conditioned on the scorer's success gate"
    )


def test_the_success_query_is_not_filtered_on_quality_score() -> None:
    """The whole point: it must count ALL arm-carrying turns, not just scored
    ones. Filtering on quality_score would reproduce the confound exactly."""
    src = _source()
    start = src.index("lessons_success")
    window = src[max(0, start - 2500):start]
    seg = window[window.rindex("SELECT lessons_arm"):]
    assert "quality_score IS NOT NULL" not in seg, (
        "the success-rate query must not filter on quality_score — that is the "
        "selection effect it exists to avoid"
    )


def test_the_quality_line_is_labelled_as_success_gated() -> None:
    """It stays, but a reader must be able to tell it is conditioned. An unlabelled
    number invites the reversed conclusion."""
    src = _source()
    assert "success-gated" in src or "scored turns only" in src, (
        "the quality comparison must say what it is conditioned on"
    )


def test_both_lines_still_require_BOTH_arms() -> None:
    """The existing guard — "one side is not a comparison, and printing it invites
    a conclusion from noise" — must hold for the new line too."""
    src = _source()
    assert src.count("len(scored) != 2") >= 1
    # Assert the INVARIANT (a both-arms length check on the success mapping), not
    # a spelling. My first version of this test demanded `!= 2` and failed against
    # a guard written as `== 2` — pinning syntax rather than behaviour, which is
    # how a test starts costing more than it protects.
    assert re.search(r"len\(succ\w*\)\s*[!=]=\s*2", src), (
        "the success line needs the same both-arms guard as the quality line"
    )


def test_the_lane_split_is_preserved() -> None:
    """3,702 machine-lane scored turns against 329 interactive ones at different
    baselines: a single aggregate would be 92% background jobs and could flip the
    sign outright. That reasoning applies to success rate identically."""
    src = _source()
    assert "MACHINE_LANE_PREFIXES" in src
    assert 'for lane in ("interactive", "machine")' in src
