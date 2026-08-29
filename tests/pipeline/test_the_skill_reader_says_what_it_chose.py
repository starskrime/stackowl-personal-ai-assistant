"""The per-query skill reader must name what it surfaced.

D10.6 Round 0 could answer almost everything about the skill corpus and NOT the
question the whole item turns on: **does the per-query reader already reach the 163
skills the frozen catalogue drops?**

If it does, the "163 unreachable skills" framing is largely answered by shipped code
and this item is small. If it only ever re-surfaces the same visible 16, the corpus
really is unreachable and the item is large. The design cannot be chosen without it.

WHY IT WAS UNANSWERABLE. `classify._gather_relevant_skills` runs on 92.5% of turns
(2,918 of 3,153) and its exit line logs `n_hits`, `block_len` and `top_sim` — at
**DEBUG**. Production runs at INFO, so in the retained logs the line does not exist
at all, and even at DEBUG it never recorded WHICH skills were chosen. Only the
block's length survives.

That is D08.1's lesson exactly, from this repo's own CLAUDE.md: *"Production runs at
INFO. A log.*.debug line does not exist when you need it... If a log line is the
evidence for a claim, it must be INFO — and run the query that would close the claim
BEFORE you need it."*

This test pins the instrument, not the behaviour: the reader is unchanged, it simply
now says what it did.
"""

from __future__ import annotations

import logging

import pytest


class _Skill:
    def __init__(self, name: str, description: str = "d", when_to_use: str = "") -> None:
        self.name = name
        self.description = description
        self.when_to_use = when_to_use


class _Store:
    def __init__(self, hits: list[tuple[_Skill, float]]) -> None:
        self._hits = hits

    async def semantic_recall(self, vector: list[float], limit: int = 3):  # noqa: ANN201
        return self._hits[:limit]


class _Services:
    """Only the two wires the reader reads off services (classify.py:387-389)."""

    def __init__(self, hits: list[tuple[_Skill, float]]) -> None:
        self.skill_store = _Store(hits)
        self.embedding_registry = self  # get() returns self; embed() below

    def get(self) -> "_Services":
        return self

    async def embed(self, _texts: object) -> list[list[float]]:
        return [[0.1, 0.2, 0.3]]


@pytest.mark.asyncio
async def test_the_reader_logs_the_NAMES_it_chose_at_INFO(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The names are the evidence. Without them the block length says nothing."""
    from stackowl.pipeline.steps import classify as mod

    hits = [
        (_Skill("evidence-brief"), 0.81),
        (_Skill("ai-news-briefing"), 0.74),
    ]

    mod.get_services = lambda: _Services(hits)  # type: ignore[assignment]

    with caplog.at_level(logging.INFO):
        out = await mod._gather_relevant_skills(
            "what happened in that incident", limit=3,
        )

    assert "evidence-brief" in out, "the block itself must still render"

    records = [r for r in caplog.records if "relevant skills" in r.getMessage().lower()]
    assert records, (
        "the reader ran and said nothing at INFO — the question 'which skills does it "
        "surface' stays unanswerable in production, which is why this item stalled"
    )
    fields = getattr(records[-1], "_fields", {})
    assert fields.get("skills") == ["evidence-brief", "ai-news-briefing"], (
        f"the chosen skill NAMES must be logged, got {fields!r}"
    )


@pytest.mark.asyncio
async def test_it_stays_silent_when_nothing_was_chosen(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A no-hit turn must not emit a misleading empty record.

    92.5% of turns produce a block; logging the other 7.5% as an INFO line with an
    empty list would put a zero into every future denominator for no reason.
    """
    from stackowl.pipeline.steps import classify as mod

    mod.get_services = lambda: _Services([])  # type: ignore[assignment]

    with caplog.at_level(logging.INFO):
        out = await mod._gather_relevant_skills("hello", limit=3)

    assert out == ""
    records = [
        r for r in caplog.records
        if "relevant skills" in r.getMessage().lower()
        and getattr(r, "_fields", {}).get("skills")
    ]
    assert not records
