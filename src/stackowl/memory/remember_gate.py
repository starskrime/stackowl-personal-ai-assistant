"""Should this be remembered, or is it already known? — ONE gate, every store.

BAKIR, 2026-08-25: "It just adds, adds, adds and memory is shrinking. There is no
similarity check. We need something lightweight and powerful to avoid remembering
the same thing again."

WHY A LADDER AND NOT A SIMILARITY SEARCH. Measured on the live stores before this
was written, the duplicates are three different KINDS:

  * staged_facts was 66% EXACT duplicates — 3,462 of 5,212 rows in 50 families.
    Free to catch. No vector needed.
  * skills carried SIX identical-token families, three differing only by
    hyphen-vs-underscore. Also free. Also no vector.
  * lessons hold 1,153 rows (22%) that are near-duplicates at cosine >= 0.90 and
    share barely half their words. Nothing but an embedding sees those.

So the cheap rungs remove most of the volume before the expensive rung is reached.
That is what makes this lightweight, and it is what makes Bakir's CROSS-STORE
decision affordable: only the minority surviving rungs 1 and 2 pays the fan-out.

WHY RUNG 3 REFUSES MORE OFTEN THAN YOU EXPECT. Cross-store cosine needs ONE vector
space and this platform does not have one yet — measured: lessons carry
embedding_model = '' on all 5,146 rows (unrecorded), reflections are split between
all-MiniLM-L6-v2 (4,772) and the DEGRADED hash fallback hash-v1-384d (18), skills
are all MiniLM. Every one is 384-dim, which is precisely the trap: the arithmetic
succeeds and the answer is meaningless. So two vectors are compared only when
their models MATCH and are non-empty. A wrong merge corrupts a reader; a skipped
comparison only wastes a row, and the asymmetry decides it.

REINFORCE, NEVER REWRITE. His words: "Keep stored, bump the counter." The stored
text must not change under a reader who already learned it, so a hit returns the
matched row and no replacement. `recall_ranker` already consumes
``reinforcement_count`` with a saturating ``1 + k*ln(1 + n)`` boost, so the signal
has a consumer waiting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from stackowl.infra.observability import log

#: SPLIT pattern, not a word pattern, and the difference is a bug this codebase
#: has now paid for twice. `\w` INCLUDES the underscore, so `\w+` reads
#: "incident_evidence_brief" as ONE token while "incident-evidence-brief" is
#: three, and the two never match. That is exactly the mistake that made an
#: earlier duplicate census report 35% when the real figure was 60%.
#: `[\W_]+` splits on any non-word character OR underscore, and stays
#: Unicode-aware — facts, owl names and preferences here are not English-only,
#: and the standing rule is that text logic must never be an English word list
#: or an ASCII-only character class.
_SPLIT_RE = re.compile(r"[\W_]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [t for t in _SPLIT_RE.split((text or "").casefold()) if t]

#: Default cosine bound for rung 3. Deliberately a DEFAULT and not a constant:
#: callers pass a per-store value, because a reflection and a preference are not
#: the same kind of text. Measured sensitivity on lessons — 42 near-duplicate
#: pairs at 0.95, 9,159 at 0.90, 87,585 at 0.85. The jump across that last 0.05
#: is why 0.90 is the working point and why it is a hypothesis until a labelled
#: sample says otherwise.
DEFAULT_THRESHOLD = 0.90


@dataclass(frozen=True)
class Candidate:
    """One row, either already stored or about to be."""

    text: str
    store: str
    row_id: str | None = None
    embedding: bytes | None = None
    #: "" means UNRECORDED, which is not the same as "matches another unrecorded".
    embedding_model: str = ""


@dataclass(frozen=True)
class Decision:
    """What to do with the candidate, and on what evidence."""

    action: Literal["insert", "reinforce"]
    matched_row_id: str | None = None
    matched_store: str | None = None
    #: Which rung decided. 0 when nothing matched.
    rung: int = 0
    similarity: float | None = None
    #: Always None. `supersede` was considered and rejected by the owner: the
    #: stored wording stays. Kept as an explicit field so a future caller cannot
    #: assume rewriting is available by omission.
    replacement_text: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def normalised_form(text: str) -> str:
    """Rung 1 key — case and punctuation folded away. Catches the 66% case."""
    return " ".join(_tokens(text))


def canonical_form(text: str) -> str:
    """Rung 2 key — the same words in any order or separator.

    Mirrors `skills.standard.canonical_key`, which shipped for the skill
    synthesizer on 2026-08-24 and is the working precedent for this gate. It is
    NOT imported from there: that function first strips a trailing ``-N``
    numbering suffix that is meaningful for skill NAMES and wrong for a sentence
    of prose. Same idea, different domain — and the two are small enough that
    sharing one would couple a memory gate to skill-naming rules.
    """
    return " ".join(sorted(_tokens(text)))


def _cosine(a: bytes, b: bytes) -> float | None:
    """Cosine of two packed float32 vectors, or None if they cannot be compared."""
    try:
        import numpy as np

        va = np.frombuffer(a, dtype=np.float32)
        vb = np.frombuffer(b, dtype=np.float32)
        if va.shape != vb.shape or va.size == 0:
            return None
        na = float(np.linalg.norm(va))
        nb = float(np.linalg.norm(vb))
        if na == 0.0 or nb == 0.0:
            return None
        return float(np.dot(va, vb) / (na * nb))
    except Exception as exc:  # B5 — a similarity check must never cost the write
        log.memory.warning("[gate] cosine failed — treating as incomparable", exc_info=exc)
        return None


def _comparable(a: Candidate, b: Candidate) -> bool:
    """Whether these two vectors live in the SAME space.

    Both must carry a vector AND the same non-empty embedding_model. The
    non-empty part is not pedantry: lessons record '' on every row, and treating
    unknown as matching unknown would compare a MiniLM vector to a degraded
    hash-fallback vector — same 384 dimensions, unrelated spaces, a confident
    wrong answer.
    """
    return bool(
        a.embedding
        and b.embedding
        and a.embedding_model
        and a.embedding_model == b.embedding_model
    )


def should_remember(
    candidate: Candidate,
    existing: list[Candidate],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> Decision:
    """Decide whether `candidate` is already known.

    Rungs run cheapest-first over the WHOLE corpus — including other stores, per
    Bakir's cross-store decision — so an exact match is decided before any cosine
    is computed. That ordering is the cost control, not an optimisation detail.
    """
    text = (candidate.text or "").strip()
    if not text or not existing:
        return Decision(action="insert")

    # --- rung 1: normalised hash ------------------------------------------------
    key = normalised_form(text)
    if key:
        for row in existing:
            if normalised_form(row.text) == key:
                return _hit(row, rung=1)

    # --- rung 2: canonical token set --------------------------------------------
    canon = canonical_form(text)
    if canon:
        for row in existing:
            if canonical_form(row.text) == canon:
                return _hit(row, rung=2)

    # --- rung 3: embeddings, only within one vector space -----------------------
    best: tuple[float, Candidate] | None = None
    skipped = 0
    for row in existing:
        if not _comparable(candidate, row):
            if row.embedding:
                skipped += 1
            continue
        sim = _cosine(candidate.embedding or b"", row.embedding or b"")
        if sim is None:
            continue
        if best is None or sim > best[0]:
            best = (sim, row)
    if best is not None and best[0] >= threshold:
        return _hit(best[1], rung=3, similarity=best[0])

    notes: tuple[str, ...] = ()
    if skipped:
        # INFO-worthy at the call site, not here: this is the honest reason a
        # semantic duplicate can still get through today.
        notes = (f"{skipped} row(s) skipped — embedding model mismatch or unrecorded",)
    return Decision(action="insert", notes=notes)


def _hit(row: Candidate, *, rung: int, similarity: float | None = None) -> Decision:
    return Decision(
        action="reinforce",
        matched_row_id=row.row_id,
        matched_store=row.store,
        rung=rung,
        similarity=similarity,
    )
