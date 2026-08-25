"""The gate that stops the platform remembering the same thing twice.

BAKIR, 2026-08-25: "It just adds, adds, adds and memory is shrinking. There is no
similarity check. We need something lightweight and powerful to avoid remembering
the same thing again." His four decisions:

  * a LADDER, cheapest rung first;
  * on a hit REINFORCE — "keep stored, bump the counter", never rewrite;
  * CROSS-STORE (his call, over my per-store recommendation);
  * compact the existing backlog automatically (separate item).

WHAT THE LADDER IS FOR. Measured on the live stores before building: the old
staged_facts was 66% EXACT duplicates — 3,462 of 5,212 rows in 50 families — which
rung 1 catches for free. Skills carried six identical-token families that only
rung 2 sees. And lessons hold 1,153 rows (22%) that are near-duplicates at cosine
>= 0.90 and share barely half their words, which ONLY rung 3 can see. Three
distinct duplicate classes, three rungs, and the cheap ones remove most of the
volume before the expensive one is reached — that is what makes it lightweight.

THE MEASUREMENT THAT SHAPED RUNG 3. Cross-store cosine needs ONE vector space, and
this platform does not have one yet: lessons record embedding_model = '' (5,146
rows, unrecorded), reflections are split across all-MiniLM-L6-v2 (4,772) and the
DEGRADED hash fallback hash-v1-384d (18), skills are all MiniLM. Every one is
384-dim, which is exactly the trap — the arithmetic succeeds and the answer is
meaningless. So rung 3 compares only rows whose embedding_model matches and is
non-empty. A wrong merge corrupts a reader; a skipped comparison only wastes a
row.
"""

from __future__ import annotations

import numpy as np
import pytest

from stackowl.memory.remember_gate import (
    Candidate,
    Decision,
    canonical_form,
    normalised_form,
    should_remember,
)

MODEL = "all-MiniLM-L6-v2"
FALLBACK = "hash-v1-384d"


def _vec(seed: int, dim: int = 384) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.tobytes()


def _near(blob: bytes, nudge: float, seed: int = 99) -> bytes:
    """A vector deliberately close to `blob` — how a paraphrase actually looks."""
    base = np.frombuffer(blob, dtype=np.float32).copy()
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(base.shape).astype(np.float32)
    noise /= np.linalg.norm(noise)
    out = base * (1.0 - nudge) + noise * nudge
    out /= np.linalg.norm(out)
    return out.astype(np.float32).tobytes()


# ---------------------------------------------------------------------------
# Rung 1 — normalised hash. Free, and it was 66% of the real problem.
# ---------------------------------------------------------------------------

def test_rung1_catches_case_and_punctuation_variants() -> None:
    assert normalised_form("Bakir prefers root-cause fixes.") == normalised_form(
        "bakir prefers root cause fixes"
    )


def test_rung1_hit_reinforces_the_stored_row() -> None:
    stored = [Candidate(row_id="a", text="Bakir prefers root-cause fixes.", store="facts")]
    d = should_remember(
        Candidate(row_id=None, text="bakir prefers root cause fixes", store="facts"),
        existing=stored,
    )
    assert d.action == "reinforce"
    assert d.matched_row_id == "a"
    assert d.rung == 1


# ---------------------------------------------------------------------------
# Rung 2 — canonical token set. The six real skill families needed this.
# ---------------------------------------------------------------------------

def test_rung2_catches_reordering_and_separators() -> None:
    assert canonical_form("incident_evidence_brief") == canonical_form(
        "incident-evidence-brief"
    )
    assert canonical_form("download-instagram-video") == canonical_form(
        "instagram-video-download"
    )


def test_rung2_hit_reports_its_own_rung() -> None:
    stored = [Candidate(row_id="b", text="report task status", store="facts")]
    d = should_remember(
        Candidate(row_id=None, text="task status report", store="facts"), existing=stored
    )
    assert d.action == "reinforce"
    assert d.rung == 2, "a reordering is not an exact match; rung 1 must have missed it"


def test_rung2_is_not_fuzzy() -> None:
    """A wrong merge corrupts a reader; a duplicate only wastes a row."""
    stored = [Candidate(row_id="c", text="scout", store="facts")]
    d = should_remember(Candidate(row_id=None, text="scouts", store="facts"), existing=stored)
    assert d.action == "insert"


# ---------------------------------------------------------------------------
# Rung 3 — embeddings. The only rung that sees a paraphrase.
# ---------------------------------------------------------------------------

def test_rung3_catches_a_paraphrase_that_shares_no_words() -> None:
    """The 22% of lessons. Two reflections at cosine 0.945 shared barely half
    their words — no exact or token-set rule will ever catch that pair."""
    v = _vec(1)
    stored = [Candidate(row_id="d", text="the shell hung indefinitely",
                        store="lessons", embedding=v, embedding_model=MODEL)]
    d = should_remember(
        Candidate(row_id=None, text="a command stalled for over two minutes",
                  store="lessons", embedding=_near(v, 0.10), embedding_model=MODEL),
        existing=stored, threshold=0.90,
    )
    assert d.action == "reinforce"
    assert d.rung == 3


def test_rung3_respects_the_threshold() -> None:
    v = _vec(2)
    stored = [Candidate(row_id="e", text="one thing", store="lessons",
                        embedding=v, embedding_model=MODEL)]
    d = should_remember(
        Candidate(row_id=None, text="a different thing", store="lessons",
                  embedding=_near(v, 0.60), embedding_model=MODEL),
        existing=stored, threshold=0.90,
    )
    assert d.action == "insert"


def test_rung3_REFUSES_to_compare_across_embedding_models() -> None:
    """THE measured trap. hash-v1-384d is the DEGRADED fallback and MiniLM is the
    real model; both are 384-dim, so the arithmetic succeeds and the answer is
    meaningless. Comparing them would merge unrelated rows with confidence."""
    v = _vec(3)
    stored = [Candidate(row_id="f", text="stored", store="reflections",
                        embedding=v, embedding_model=FALLBACK)]
    d = should_remember(
        Candidate(row_id=None, text="incoming", store="reflections",
                  embedding=_near(v, 0.01), embedding_model=MODEL),
        existing=stored, threshold=0.90,
    )
    assert d.action == "insert", "vectors from different models are not comparable"


def test_rung3_REFUSES_when_the_model_is_unrecorded() -> None:
    """lessons carries embedding_model = '' on all 5,146 rows. Unknown is not a
    match for unknown — it is unknown."""
    v = _vec(4)
    stored = [Candidate(row_id="g", text="stored", store="lessons",
                        embedding=v, embedding_model="")]
    d = should_remember(
        Candidate(row_id=None, text="incoming", store="lessons",
                  embedding=_near(v, 0.01), embedding_model=""),
        existing=stored, threshold=0.90,
    )
    assert d.action == "insert"


def test_a_store_with_no_vectors_still_gets_rungs_1_and_2() -> None:
    """user_preferences has no embedding column at all. The cheap rungs must work
    with no vector and no schema change."""
    stored = [Candidate(row_id="h", text="Prefers dark mode", store="preferences")]
    d = should_remember(
        Candidate(row_id=None, text="prefers dark mode!", store="preferences"),
        existing=stored,
    )
    assert d.action == "reinforce"
    assert d.rung == 1


# ---------------------------------------------------------------------------
# Cross-store — Bakir's call, over my per-store recommendation
# ---------------------------------------------------------------------------

def test_a_duplicate_is_found_in_ANOTHER_store() -> None:
    """His reason: "a preference and a fact can say the same thing"."""
    stored = [Candidate(row_id="i", text="Bakir prefers root-cause fixes",
                        store="preferences")]
    d = should_remember(
        Candidate(row_id=None, text="bakir prefers root cause fixes", store="facts"),
        existing=stored,
    )
    assert d.action == "reinforce"
    assert d.matched_store == "preferences", "the match must name where it was found"


def test_the_cheap_rungs_run_before_any_vector_work() -> None:
    """What makes cross-store affordable: an exact match must never reach rung 3,
    so the fan-out cost is paid only by the minority that survive rungs 1 and 2."""
    v = _vec(5)
    stored = [Candidate(row_id="j", text="same text", store="lessons",
                        embedding=v, embedding_model=MODEL)]
    d = should_remember(
        Candidate(row_id=None, text="Same text.", store="facts",
                  embedding=_near(v, 0.5), embedding_model=MODEL),
        existing=stored, threshold=0.90,
    )
    assert d.rung == 1, "an exact match must be decided before any cosine is computed"


# ---------------------------------------------------------------------------
# The contract Bakir chose
# ---------------------------------------------------------------------------

def test_a_hit_NEVER_rewrites_the_stored_text() -> None:
    """"Keep stored, bump the counter." The stored text must not change under a
    reader who already learned it."""
    # MY TEST WAS WRONG FIRST: the two texts differed by the word "STRONGLY", so
    # no rung could match them without a vector, and the gate correctly said
    # insert. The contract under test is what a HIT returns, so the fixture now
    # actually produces a hit.
    stored = [Candidate(row_id="k", text="Bakir prefers root-cause fixes.", store="facts")]
    d = should_remember(
        Candidate(row_id=None, text="bakir prefers root cause fixes", store="facts"),
        existing=stored,
    )
    assert d.action == "reinforce"
    assert d.replacement_text is None, (
        "supersede was considered and rejected — the stored wording stays"
    )


def test_a_genuinely_new_fact_is_inserted() -> None:
    """The gate must not turn memory off."""
    stored = [Candidate(row_id="l", text="Bakir prefers root-cause fixes", store="facts")]
    d = should_remember(
        Candidate(row_id=None, text="The dentist is on Preston Road", store="facts"),
        existing=stored,
    )
    assert d.action == "insert"
    assert d.matched_row_id is None


def test_an_empty_corpus_inserts() -> None:
    d = should_remember(Candidate(row_id=None, text="anything", store="facts"), existing=[])
    assert d.action == "insert"


@pytest.mark.parametrize("odd", ["", "   ", "!!!", "…"])
def test_odd_input_never_raises(odd: str) -> None:
    d = should_remember(Candidate(row_id=None, text=odd, store="facts"), existing=[])
    assert isinstance(d, Decision)
