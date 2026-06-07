"""Score an owl's owned skills against the current query embedding (cosine) plus a cross-turn
hysteresis bonus. Pure-ish (no I/O beyond the in-memory tracker). Feeds assign_tiers()."""
from __future__ import annotations

from typing import Protocol

from stackowl.memory.sqlite_helpers import cosine_similarity
from stackowl.skills.skill_focus import SkillFocusTracker


class _Embeddable(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def embedding(self) -> list[float] | None: ...


def score_owned_skills(
    owned: list[_Embeddable],
    *,
    query_embedding: tuple[float, ...],
    tracker: SkillFocusTracker,
    owl: str,
    session: str,
    turn: int,
) -> dict[str, float]:
    """Return {skill_name: score}. score = cosine(query, skill.embedding) + hysteresis bonus.
    A skill with no embedding scores -1.0 (sinks to CATALOG). Never raises."""
    q = list(query_embedding)
    scores: dict[str, float] = {}
    for sk in owned:
        if sk.embedding is None:
            scores[sk.name] = -1.0
            continue
        cos = cosine_similarity(q, list(sk.embedding))
        base = cos if cos is not None else -1.0
        bonus = tracker.bonus_for(owl, session, sk.name, turn)
        scores[sk.name] = base + bonus
    return scores
