"""A relevance floor must sit ABOVE what unrelated text scores, not inside it.

MEASURED 2026-09-03 against the live archive (202 staged rows carrying a
384-dimension all-MiniLM-L6-v2 vector), by scoring three GIBBERISH probes to
establish what "no relationship at all" looks like in this space::

    "zzzz qqqq xyzzy plugh frotz nonsense unrelated gibberish"   best 0.284
    "blorp fnord snorgle wibble quux grault garply waldo thud"   best 0.230
    "aaaa bbbb cccc dddd eeee ffff gggg hhhh iiii jjjj kkkk"     best 0.209

So 0.284 is this archive's NOISE CEILING: the best score meaningless text can
reach. The floor shipped at 0.25 — *below* it. A threshold inside the noise band
is not a threshold.

WHAT IT LET THROUGH. Scoring real queries of both shapes the platform actually
runs::

    query                 top    hits kept @0.25   of those, BELOW 0.284
    his own question     0.518        18                    0
    "job market search"  0.485        18                    0
    incident/db-lock     0.364        13                    7
    goal/execution       0.343        11                    5
    incident/RCA         0.384        12                    4

His own questions are answered well — the archive is HIS conversations, so it
genuinely holds the answers. The machine lanes are the defect: roughly HALF of
every memory injected into an incident diagnosis was less related to the prompt
than gibberish is to the archive. The live log shows all 22 recalls since the
change came from those lanes, every one saturating its budget at 18.

THE CAUSE, and it is not "the number was too small". A cosine score has no
meaning in isolation — only against what THIS corpus scores for unrelated text.
The floor was chosen to be safely permissive without ever measuring that band, so
it could not distinguish "the best match in a corpus that holds nothing relevant"
from "a genuinely relevant match". For a query the archive can answer the top hit
is ~0.50; for one it cannot it is ~0.35, and a fixed 0.25 admits both.

WHY 0.35 AND NOT HIGHER, measured across the same queries::

    floor   nonsense   his questions   machine lanes
    0.25        1        18, 18          13, 11, 12    <- shipped, the defect
    0.30        0        18, 18           4,  4,  4
    0.35        0        11, 12           1,  0,  2    <- chosen
    0.40        0         4,  7           0,  0,  0    <- breaks HIS recall

0.40 starts cutting the recall that works. 0.30 still leaves four noise hits on
every machine turn. 0.35 is the only value that clears the noise ceiling with
margin while leaving his own questions answerable.

RAISING IT STRANDS NOTHING. ``recall`` runs the semantic pass first and tops up
with the substring scan for whatever budget is left, so a hit the floor now
rejects is not lost — it is left to the path that requires a literal phrase and
is therefore precise. That property is asserted below, because it is the whole
reason this change is safe.

AND NOTHING COULD HAVE TOLD US. Every line reporting this ran at
``log.memory.debug`` while production runs at INFO, and none carried a score, so
the only way to learn the floor was miscalibrated was the offline script that
found it. The exit line is now INFO and carries the top similarity alongside the
floor it was judged against — the next drift is visible from the logs.

SCOPE, checked and deliberately not widened. ``skills.store.semantic_recall``
takes ``min_similarity=0.0`` and no caller overrides it, which looks like the same
defect and is not: it RANKS a small curated catalogue to pick the best available
skills for a turn, where the weakest match still beats offering none. This is a
RELEVANCE filter over an open-ended archive. Sharing one constant between them
would have been a second bug wearing this one's clothes.
"""

from __future__ import annotations

import logging
import struct

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.sqlite_helpers import (
    _MIN_SEMANTIC_SIMILARITY,
    staged_semantic_recall,
)


#: The best cosine any of three gibberish probes reached against the live
#: 202-row archive. Anything at or under this is indistinguishable from noise.
MEASURED_NOISE_CEILING = 0.284

#: The weakest top-hit among queries the archive genuinely answers.
WEAKEST_GENUINE_TOP = 0.485

#: Machine-lane hits kept at the shipped 0.25 floor, and at the chosen one. A
#: single constant cannot separate the two populations completely — the best hit
#: of an unanswerable query (0.384) outranks the 12th hit of an answerable one —
#: so the floor is chosen to collapse the noise while leaving his recall usable.
MACHINE_HITS_AT_OLD_FLOOR = (13, 11, 12)
MACHINE_HITS_AT_NEW_FLOOR = (1, 0, 2)


def _unit(*xs: float) -> bytes:
    return struct.pack(f"{len(xs)}f", *xs)


def _at_cosine(c: float) -> bytes:
    """A unit vector whose cosine against ``[1, 0, 0]`` is exactly ``c``."""
    return _unit(c, (1.0 - c * c) ** 0.5, 0.0)


QUERY = [1.0, 0.0, 0.0]


def _exit_fields(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
    """The exit line's STRUCTURED fields.

    ``caplog.text`` renders only the message; this codebase carries its evidence
    in ``extra={"_fields": …}``, so asserting on the text would pass against a
    line that reports nothing at all.
    """
    for record in reversed(caplog.records):
        if "staged_semantic_recall: exit" in record.getMessage():
            return dict(getattr(record, "_fields", {}) or {})
    raise AssertionError("staged_semantic_recall logged no exit line")


async def _stage(
    db: DbPool, fact_id: str, content: str, embedding: bytes | None,
) -> None:
    await db.execute(
        "INSERT INTO staged_facts (fact_id, content, source_type, source_ref, "
        "confidence, staged_at, status, owner_id, embedding, embedding_model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (fact_id, content, "conversation", "ref", 1.0,
         "2026-09-03T00:00:00+00:00", "staged", "principal-default",
         embedding, "all-MiniLM-L6-v2" if embedding else None),
    )


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_hit_inside_the_noise_band_is_not_recalled(tmp_db: DbPool) -> None:
    """THE DEFECT. 0.30 beat the shipped 0.25 floor, and gibberish reaches 0.284
    against this archive — so a row scoring 0.30 carries no more relationship to
    the question than nonsense does. Seven of thirteen memories injected into a
    live incident diagnosis were in exactly this band."""
    await _stage(tmp_db, "f-noise", "unrelated to the question", _at_cosine(0.30))

    hits = await staged_semantic_recall(tmp_db, QUERY, limit=5)

    assert hits == [], (
        "a row scoring 0.30 was recalled — gibberish reaches 0.284 against the "
        "live archive, so this is noise presented to the model as memory"
    )


@pytest.mark.asyncio
async def test_a_genuine_match_is_still_recalled(tmp_db: DbPool) -> None:
    """The other direction, and the reason the floor is not simply raised until
    nothing passes: queries the archive CAN answer top out around 0.50, and his
    own questions are all of that shape."""
    await _stage(tmp_db, "f-real", "the fact that answers the question", _at_cosine(0.50))

    hits = await staged_semantic_recall(tmp_db, QUERY, limit=5)

    assert [h.fact_id for h in hits] == ["f-real"]


@pytest.mark.asyncio
async def test_the_noise_is_dropped_while_the_signal_survives(tmp_db: DbPool) -> None:
    """Both at once — the shape of a real machine-lane recall, where one row is
    genuinely related and the rest merely score above zero."""
    await _stage(tmp_db, "f-real", "genuinely about the question", _at_cosine(0.52))
    for i, c in enumerate((0.34, 0.31, 0.28, 0.22)):
        await _stage(tmp_db, f"f-noise-{i}", "not about the question", _at_cosine(c))

    hits = await staged_semantic_recall(tmp_db, QUERY, limit=18)

    assert [h.fact_id for h in hits] == ["f-real"], (
        f"expected only the genuine match; got {[h.fact_id for h in hits]}"
    )


# --------------------------------------------------------------------------- #
# The constant itself — so it can never silently sit inside the noise again     #
# --------------------------------------------------------------------------- #


def test_the_floor_clears_the_measured_noise_ceiling() -> None:
    """THE ROOT CAUSE, asserted directly. The shipped 0.25 was BELOW the 0.284
    that meaningless text reaches. A floor inside the noise band cannot separate
    relevance from coincidence however well the rest of the function works."""
    assert _MIN_SEMANTIC_SIMILARITY > MEASURED_NOISE_CEILING, (
        f"the recall floor ({_MIN_SEMANTIC_SIMILARITY}) is at or below what pure "
        f"gibberish scores against the live archive ({MEASURED_NOISE_CEILING}) — "
        "every recall then admits rows no more related than nonsense"
    )
    # It clears the ceiling with margin rather than by a hair: the probes are a
    # sample of "unrelated", not its maximum, so sitting just above the observed
    # best would be inside the band on the next unlucky query.
    assert _MIN_SEMANTIC_SIMILARITY >= MEASURED_NOISE_CEILING + 0.05, (
        f"the floor ({_MIN_SEMANTIC_SIMILARITY}) clears the observed noise "
        f"ceiling ({MEASURED_NOISE_CEILING}) by too little to survive a probe "
        "that scores higher than the three that were measured"
    )


def test_one_constant_cannot_fully_separate_the_two_populations() -> None:
    """THE HONEST LIMIT of this fix, recorded so the residue is not mistaken for
    completeness. The best hit of a query the archive CANNOT answer (0.384)
    outranks the 12th hit of one it can, so no single threshold removes all noise
    while keeping all signal. 0.35 collapses the machine lanes from 13/11/12 hits
    to 1/0/2 — an 85% cut — and leaves his own questions 11 and 12. What remains
    is a handful of weak hits on unanswerable queries, which is a far smaller
    error than the one it replaces, and is why the exit line now carries the
    score: the residue is measurable rather than assumed."""
    old_total = sum(MACHINE_HITS_AT_OLD_FLOOR)
    new_total = sum(MACHINE_HITS_AT_NEW_FLOOR)
    assert new_total <= old_total * 0.2, (
        "the chosen floor no longer removes the bulk of the machine lanes' "
        f"noise: {old_total} hits became {new_total}"
    )


def test_the_floor_does_not_break_the_recall_that_works() -> None:
    """The opposite failure, and it is the one that would be invisible: pushed to
    0.40 the floor cut his own questions from 18 hits to 4 and 7. The archive is
    HIS conversations and answering them is what it is for."""
    assert _MIN_SEMANTIC_SIMILARITY < WEAKEST_GENUINE_TOP, (
        f"the floor ({_MIN_SEMANTIC_SIMILARITY}) is at or above the weakest top "
        f"hit of a question the archive genuinely answers ({WEAKEST_GENUINE_TOP}) "
        "— his own recall is being cut to suppress the machine lanes' noise"
    )


# --------------------------------------------------------------------------- #
# Why raising it is safe, and how the next drift becomes visible                #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_rejected_row_is_still_reachable_by_literal_phrase(
    tmp_db: DbPool,
) -> None:
    """THE SAFETY PROPERTY. ``recall`` runs semantic first and hands the leftover
    budget to the substring scan, so a row the floor rejects is not stranded —
    it is left to the path that demands a literal phrase and is precise. Without
    this, raising the floor would be the "made it unreachable and called it a
    cleanup" mistake the substring scan was kept to prevent."""
    from stackowl.memory.sqlite_bridge import SqliteMemoryBridge

    await _stage(tmp_db, "f-weak", "The bastion host is 192.168.1.81", _at_cosine(0.30))

    found = await SqliteMemoryBridge(tmp_db).recall("bastion", limit=10)

    assert any(r.fact_id == "f-weak" for r in found), (
        "a row below the semantic floor became unreachable — the substring scan "
        "must still find it by its literal words"
    )


@pytest.mark.asyncio
async def test_the_exit_line_reports_the_score_it_judged(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture,
) -> None:
    """WHY THIS WENT UNNOTICED. Every line that would have shown the floor was
    miscalibrated ran at DEBUG, and none carried a score — production runs at
    INFO, so no volume of live traffic could ever have revealed it. The top
    similarity and the floor it was judged against are now both on an INFO line,
    which is what makes the NEXT drift measurable instead of invisible."""
    await _stage(tmp_db, "f-real", "answers the question", _at_cosine(0.52))

    with caplog.at_level(logging.INFO, logger="stackowl.memory"):
        await staged_semantic_recall(tmp_db, QUERY, limit=5)

    fields = _exit_fields(caplog)
    assert fields.get("top_similarity") == pytest.approx(0.52, abs=1e-3), (
        "recall exits without reporting the best score it saw — the only "
        f"instrument that can show this floor drifting out of calibration: {fields}"
    )
    assert fields.get("floor") == _MIN_SEMANTIC_SIMILARITY


@pytest.mark.asyncio
async def test_an_empty_corpus_still_reports_rather_than_going_silent(
    tmp_db: DbPool, caplog: pytest.LogCaptureFixture,
) -> None:
    """A recall that finds nothing is exactly the case worth seeing in the log —
    an instrument that only speaks on success cannot show a floor set too high."""
    await _stage(tmp_db, "f-noise", "unrelated", _at_cosine(0.10))

    with caplog.at_level(logging.INFO, logger="stackowl.memory"):
        hits = await staged_semantic_recall(tmp_db, QUERY, limit=5)

    assert hits == []
    fields = _exit_fields(caplog)
    assert fields.get("top_similarity") == pytest.approx(0.10, abs=1e-3), (
        "a recall that returned nothing reported no score — so a floor set too "
        f"HIGH would be silent exactly when it is wrong: {fields}"
    )
