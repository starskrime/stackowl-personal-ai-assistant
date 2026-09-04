"""Every rung of the mint-time duplicate gate was LEXICAL, and skills are the
artifact where two different sets of words describe one capability.

MEASURED on the live corpus 2026-09-04. 39 registered skills, 25 of them
learned — and those 25 are roughly EIGHT concepts:

    VERIFIER (7)  rca-evidence-verifier, rca_evidence_brief_verifier,
                  verify_rca_evidence, verify-incident-hypothesis,
                  evidence-brief-verifier, incident_hypothesis_evidence_check,
                  evidence_verifier_rejection
    GATHERER (3)  evidence_brief, rca_evidence_brief, incident-evidence-brief
    OWLS (2)      owl-name-collision-check, owls-list
    OWLBUILD (2)  incident_owl_build, incident_owl_build_stop
                  — byte-identical descriptions

None of these collide under the existing gate, and that is not a bug in the
gate: `base_name` answers "is this a `-N` variant?", `canonical_key` answers "is
this the same name, rearranged?", and `_cluster_already_covered` answers "did we
learn this from the same traces?". All three are true answers to lexical
questions. `verify_rca_evidence` and `evidence-brief-verifier` share no tokens
at all, so no arrangement of their letters will ever make them equal.

This is the third rung of one ladder: `base_name` missed rearrangements ->
`canonical_key` was added (ESC-52) -> `canonical_key` misses synonyms and
supersets. Each fix extended the LEXICAL family instead of changing the KIND of
question asked.

The instrument to ask a different kind was already built, populated and unused:
`store.semantic_recall` does cosine over enabled skills, and 39 of 39 skills
carry an embedding. Built but not wired.

THE THRESHOLD IS MEASURED, NOT CHOSEN. Over all 741 pairs of the live corpus:
confirmed duplicates score 0.828-0.952, everything else tops out at 0.875. At
0.90 the gate catches 6 of 26 known duplicate pairs and flags ZERO non-
duplicates. It is deliberately conservative — a missed duplicate is recoverable
and visible, while a wrongly suppressed skill is a capability that never exists
and nothing ever reports.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore
from stackowl.skills.synthesizer import _SEMANTIC_TWIN_MIN, SkillSynthesizer


def _loaded(name: str, desc: str = "d") -> LoadedSkill:
    return LoadedSkill(
        manifest=SkillManifest(name=name, description=desc, source="learned"),
        path=Path("/tmp/x"), body="b", tools_registered=0, owls_registered=0, tool_names=(),
    )


class _Cluster:
    sequence = ("web_fetch", "shell")
    size = 3


class _Provider:
    """Returns a vector at a chosen cosine from the stored one."""

    model_name = "fake-embed"

    def __init__(self, vec: list[float]) -> None:
        self._vec = vec

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec for _ in texts]


class _Registry:
    def __init__(self, provider: _Provider) -> None:
        self._p = provider

    def get(self) -> _Provider:
        return self._p


class _Synth:
    """The gate bound to a real store — a full synthesizer needs a provider, an
    outcome store, a consent gate and a workspace root, none of which this
    behaviour touches."""

    def __init__(self, store: SkillIndexStore, registry: _Registry | None = None) -> None:
        self._skills = store
        self._embedding_registry = registry

    _reinforce_if_known = SkillSynthesizer._reinforce_if_known
    _semantic_twin = SkillSynthesizer._semantic_twin


# A stored vector and two queries: one clearly above the threshold, one clearly
# below. Cosine is computed on the real path (store.semantic_recall), not here.
_STORED = [1.0, 0.0, 0.0, 0.0]
_NEAR = [0.97, 0.24, 0.0, 0.0]     # cos ~= 0.970
_FAR = [0.60, 0.80, 0.0, 0.0]      # cos ~= 0.600


async def _seed(store: SkillIndexStore, name: str) -> str:
    sid = await store.upsert(_loaded(name))
    await store.set_embeddings_batch([(sid, _STORED, "fake-embed")])
    return sid


@pytest.mark.asyncio
async def test_a_twin_with_no_shared_tokens_is_caught(tmp_db, caplog) -> None:
    """The case the lexical rungs cannot reach, and the whole point of the item."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, "evidence_brief_verifier")

    synth = _Synth(store, _Registry(_Provider(_NEAR)))
    with caplog.at_level(logging.INFO, logger="stackowl.skills"):
        known = await synth._reinforce_if_known(
            "verify_incident_hypothesis", _Cluster(),
            embed_text="verify_incident_hypothesis\nJudge a hypothesis against a brief.",
        )

    assert known is True, "a semantic twin must reinforce, not mint an eighth copy"
    hit = [r for r in caplog.records if "ALREADY KNOWN" in r.getMessage()]
    assert hit, "the skip must be visible at INFO — it is the only evidence the rung fires"
    fields = getattr(hit[0], "_fields", {})
    assert fields.get("matched_on") == "semantic", "the rung that fired must be named"
    assert fields.get("similarity") is not None, (
        "the score is what makes the threshold auditable after the fact"
    )


@pytest.mark.asyncio
async def test_a_genuinely_different_lesson_is_still_minted(tmp_db) -> None:
    """The control. A gate that catches everything has not been shown to work."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, "evidence_brief_verifier")

    synth = _Synth(store, _Registry(_Provider(_FAR)))
    known = await synth._reinforce_if_known(
        "summarise_a_pdf", _Cluster(), embed_text="summarise_a_pdf\nSummarise a PDF.",
    )

    assert known is False, "an unrelated lesson must still be minted"


@pytest.mark.asyncio
async def test_the_lexical_rungs_still_work_without_an_embedder(tmp_db) -> None:
    """The new rung must not become a prerequisite for the old ones.

    `_embedding_registry` is optional on the synthesizer and is None in several
    construction paths, so a semantic rung that raised or short-circuited would
    silently turn the whole gate off.
    """
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("recover_tool_search"))

    synth = _Synth(store, registry=None)
    assert await synth._reinforce_if_known("recover_tool_search", _Cluster()) is True
    assert await synth._reinforce_if_known("brand_new", _Cluster()) is False


@pytest.mark.asyncio
async def test_a_missing_embed_text_does_not_reach_the_embedder(tmp_db) -> None:
    """Callers that have no description yet must not pay for an embedding call,
    and must not match on an empty string."""
    store = SkillIndexStore(tmp_db)
    await _seed(store, "evidence_brief_verifier")

    class _Exploding(_Provider):
        async def embed(self, texts):  # noqa: ANN001, ANN202
            raise AssertionError("must not embed when there is no text")

    synth = _Synth(store, _Registry(_Exploding(_NEAR)))
    assert await synth._reinforce_if_known("unrelated_name", _Cluster()) is False


def test_the_threshold_sits_above_the_measured_non_duplicate_ceiling() -> None:
    """0.875 is the highest cosine between two skills that are NOT duplicates,
    over all 741 pairs of the live corpus on 2026-09-04. A threshold at or below
    it would suppress a real skill, which is the one failure this gate must not
    have — a duplicate is visible and recoverable, a skill that was never minted
    is neither."""
    assert _SEMANTIC_TWIN_MIN > 0.875
