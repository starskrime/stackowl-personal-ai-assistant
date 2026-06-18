# Ranked, Applied & Explained Lessons — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank mined tool-heuristics by confidence when injecting them as lessons, let the model honestly self-report when it acted on one, and surface that to the user — closing learning pillar ③ and explainability pillar ④ in one thin slice.

**Architecture:** A pure UCB ranking function reorders heuristic lesson-hits at injection (`classify._gather_lessons`). The surfaced lessons (with turn-local ids `L1…`) are stashed in a turn-scoped ContextVar. A new non-consequential meta-tool `note_applied_lesson` lets the model record that it acted on a cited lesson; the record lands in a second ContextVar sink. A pre-delivery render step `surface_applied_lessons` drains the sink and appends one localized line to the user's response — only when the model actually reported. Honesty is structural: no tool call → no claim.

**Tech Stack:** Python 3.13, Pydantic v2 (frozen models), asyncio, contextvars, pytest. Stores already in place: SQLite `tool_heuristics`, LanceDB lessons index.

**Design deviations from the spec (strictly reducing — surfaced during plan-time recon):**
1. **No new `PipelineState` fields.** Tools cannot reach `PipelineState` (they only read ambient `TraceContext`). Transport surfaced→tool→render via a dedicated turn-scoped ContextVar module instead. Fewer touch points, same behavior.
2. **No new `LessonHit` fields.** `LessonHit.metadata: dict` already exists and already carries `evidence_count` for heuristic lessons. Ranking reads from `metadata`; the miner just adds `mean_quality` to it (one line).

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/pipeline/lesson_context.py` | **Create** | Turn-scoped ContextVars + `SurfacedLesson`/`AppliedLesson` dataclasses + bind/reset/set_surfaced/get_surfaced/record_applied/drain_applied |
| `src/stackowl/learning/heuristic_ranking.py` | **Create** | Pure `rank_lessons(hits)` — UCB reorder of heuristic hits |
| `src/stackowl/learning/tool_outcome_miner.py` | Modify (~156) | Add `mean_quality` to heuristic lesson metadata |
| `src/stackowl/pipeline/steps/classify.py` | Modify (`_gather_lessons`) | Rank, assign `L#` ids, add contract line, `set_surfaced(...)` |
| `src/stackowl/tools/meta/note_applied_lesson.py` | **Create** | The self-report tool |
| `src/stackowl/tools/registry.py` | Modify (`with_defaults`) | Register the new tool |
| `src/stackowl/setup/localize.py` | Modify (`_STRINGS`) | `self_heal_applied_lesson` key (en/de/fr) |
| `src/stackowl/pipeline/applied_lessons.py` | **Create** | `surface_applied_lessons(state)` render step |
| `src/stackowl/pipeline/backends/asyncio_backend.py` | Modify | bind/reset + call render |
| `src/stackowl/pipeline/backends/langgraph_backend.py` | Modify | bind/reset + call render |
| `tests/journeys/test_learning_explainability_journey.py` | **Create** | Gateway FR1–FR6 |
| `tests/learning/test_heuristic_ranking.py` | **Create** | Ranking unit |
| `tests/pipeline/test_lesson_context.py` | **Create** | ContextVar unit |
| `tests/pipeline/test_applied_lessons_render.py` | **Create** | Render-step unit |

---

## Task 1: Turn-scoped lesson ContextVar module

**Files:**
- Create: `src/stackowl/pipeline/lesson_context.py`
- Test: `tests/pipeline/test_lesson_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_lesson_context.py
from stackowl.pipeline import lesson_context as lc


def _surfaced():
    return (
        lc.SurfacedLesson(lesson_id="L1", source_type="tool_heuristic",
                          content="browse_url tends to fail on PDF hosts", similarity=0.9),
        lc.SurfacedLesson(lesson_id="L2", source_type="reflection",
                          content="prefer fetch over scrape", similarity=0.7),
    )


def test_record_known_id_lands_in_sink_with_summary():
    token = lc.bind()
    try:
        lc.set_surfaced(_surfaced())
        matched = lc.record_applied("L1", "used the fetch tool instead of browse_url")
        assert matched is not None and matched.lesson_id == "L1"
        applied = lc.drain_applied()
        assert len(applied) == 1
        assert applied[0].lesson_id == "L1"
        assert applied[0].what_you_did == "used the fetch tool instead of browse_url"
        assert applied[0].lesson_summary == "browse_url tends to fail on PDF hosts"
    finally:
        lc.reset(token)


def test_record_unknown_id_is_recorded_with_null_summary():
    token = lc.bind()
    try:
        lc.set_surfaced(_surfaced())
        matched = lc.record_applied("L9", "did a thing")
        assert matched is None
        applied = lc.drain_applied()
        assert len(applied) == 1 and applied[0].lesson_summary is None
        assert applied[0].what_you_did == "did a thing"
    finally:
        lc.reset(token)


def test_record_without_bind_is_noop():
    # No bind() — sink is unbound; record must not raise and drain stays empty.
    assert lc.record_applied("L1", "x") is None
    assert lc.drain_applied() == ()


def test_reset_clears_state():
    token = lc.bind()
    lc.set_surfaced(_surfaced())
    lc.record_applied("L1", "x")
    lc.reset(token)
    assert lc.drain_applied() == ()
    assert lc.get_surfaced() == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_lesson_context.py -q`
Expected: FAIL — `ModuleNotFoundError: stackowl.pipeline.lesson_context`

- [ ] **Step 3: Write minimal implementation**

```python
# src/stackowl/pipeline/lesson_context.py
"""Turn-scoped carrier for surfaced lessons + the model's self-reported uses.

Tools cannot reach the immutable ``PipelineState``; they only see ambient
context. This module mirrors the ``TraceContext`` ContextVar idiom to carry, for
the duration of ONE turn:
  * the lessons the classify step surfaced (so a tool can resolve an ``L#`` id), and
  * the lessons the model reported it acted on (so the delivery step can explain).

The backend ``bind()``s a fresh, empty context at turn start and ``reset()``s it
in a ``finally`` — so nothing leaks across turns or across concurrent turns
(each turn runs in its own async task; ContextVars are per-context).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from stackowl.infra.observability import log


@dataclass(frozen=True)
class SurfacedLesson:
    """A lesson injected into the prompt this turn, addressable by a turn-local id."""

    lesson_id: str          # turn-local short id, e.g. "L1"
    source_type: str
    content: str
    similarity: float


@dataclass(frozen=True)
class AppliedLesson:
    """The model's self-report that it acted on a surfaced lesson."""

    lesson_id: str
    what_you_did: str
    lesson_summary: str | None   # the surfaced content, or None if the id was unknown


_surfaced: ContextVar[tuple[SurfacedLesson, ...]] = ContextVar(
    "surfaced_lessons", default=(),
)
# None == NOT bound this turn (record_applied is then a no-op). A bound turn holds a tuple.
_applied: ContextVar[tuple[AppliedLesson, ...] | None] = ContextVar(
    "applied_lessons", default=None,
)


@dataclass
class _LessonToken:
    surfaced: Token[tuple[SurfacedLesson, ...]]
    applied: Token[tuple[AppliedLesson, ...] | None]


def bind() -> _LessonToken:
    """Install a fresh empty lesson context for one turn. Returns a reset token."""
    return _LessonToken(surfaced=_surfaced.set(()), applied=_applied.set(()))


def reset(token: _LessonToken) -> None:
    """Restore the prior lesson context (call in a ``finally``)."""
    _surfaced.reset(token.surfaced)
    _applied.reset(token.applied)


def set_surfaced(lessons: tuple[SurfacedLesson, ...]) -> None:
    """Record the lessons surfaced this turn (called by the classify step)."""
    _surfaced.set(tuple(lessons))


def get_surfaced() -> tuple[SurfacedLesson, ...]:
    return _surfaced.get()


def record_applied(lesson_id: str, what_you_did: str) -> SurfacedLesson | None:
    """Record that the model acted on ``lesson_id``. Returns the matched surfaced
    lesson (or None if the id was not surfaced this turn). No-op if unbound."""
    current = _applied.get()
    if current is None:
        log.engine.debug(
            "[lesson_context] record_applied: unbound turn — ignoring",
            extra={"_fields": {"lesson_id": lesson_id}},
        )
        return None
    match = next((s for s in _surfaced.get() if s.lesson_id == lesson_id), None)
    if match is None:
        log.engine.info(
            "[lesson_context] record_applied: unknown lesson id",
            extra={"_fields": {"lesson_id": lesson_id}},
        )
    _applied.set((*current, AppliedLesson(
        lesson_id=lesson_id,
        what_you_did=what_you_did,
        lesson_summary=match.content if match is not None else None,
    )))
    return match


def drain_applied() -> tuple[AppliedLesson, ...]:
    """Return the applied-lesson reports for this turn (empty if none/unbound)."""
    return _applied.get() or ()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_lesson_context.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/lesson_context.py tests/pipeline/test_lesson_context.py
git commit -m "feat(v2): turn-scoped lesson_context — carry surfaced+applied lessons via ContextVar"
```

---

## Task 2: UCB ranking of heuristic lesson-hits

**Files:**
- Create: `src/stackowl/learning/heuristic_ranking.py`
- Test: `tests/learning/test_heuristic_ranking.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/learning/test_heuristic_ranking.py
from stackowl.learning.heuristic_ranking import rank_lessons
from stackowl.learning.lesson import LessonHit


def _hit(ref, sim, source="tool_heuristic", evidence=None, quality=None):
    md: dict[str, object] = {}
    if evidence is not None:
        md["evidence_count"] = evidence
    if quality is not None:
        md["mean_quality"] = quality
    return LessonHit(lesson_id=ref, source_type=source, source_ref=ref,
                     content=f"lesson {ref}", similarity=sim, metadata=md)


def test_well_evidenced_high_similarity_ranks_above_low_evidence():
    hits = [
        _hit("a", sim=0.60, evidence=3),    # low evidence → big exploration bonus
        _hit("b", sim=0.80, evidence=50),   # strong similarity, well evidenced
    ]
    ranked = rank_lessons(hits)
    assert ranked[0].source_ref == "b"


def test_non_heuristic_hits_kept_after_heuristics_in_original_order():
    hits = [
        _hit("r1", sim=0.95, source="reflection"),
        _hit("h1", sim=0.50, evidence=10),
        _hit("r2", sim=0.40, source="reflection"),
    ]
    ranked = rank_lessons(hits)
    assert [h.source_ref for h in ranked[1:]] == ["r1", "r2"]   # reflections after, original order
    assert ranked[0].source_ref == "h1"                          # heuristic first


def test_missing_evidence_metadata_scores_as_similarity_only():
    hits = [_hit("x", sim=0.30, evidence=None), _hit("y", sim=0.40, evidence=None)]
    ranked = rank_lessons(hits)
    assert [h.source_ref for h in ranked] == ["y", "x"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/learning/test_heuristic_ranking.py -q`
Expected: FAIL — `ModuleNotFoundError: stackowl.learning.heuristic_ranking`

- [ ] **Step 3: Write minimal implementation**

```python
# src/stackowl/learning/heuristic_ranking.py
"""Confidence-aware ranking of tool-heuristic lesson hits (UCB-style).

Pillar ③: a noisy, low-evidence heuristic should not outrank a well-proven one
purely on semantic similarity. We reorder ONLY ``tool_heuristic`` hits by

    score(h) = similarity(h) + c * sqrt( ln(N) / evidence_count(h) )

with c = sqrt(2) and N = sum of evidence over the heuristic candidates (>= e, so
the log is non-negative). High similarity dominates; the exploration term gives a
bounded bonus to under-observed heuristics. Hits with no ``evidence_count`` in
metadata (legacy rows / non-heuristic) score as similarity-only — fail-safe.
Non-heuristic hits keep their original relative order, appended after heuristics.
"""

from __future__ import annotations

import math

from stackowl.infra.observability import log
from stackowl.learning.lesson import LessonHit

_HEURISTIC_SOURCE = "tool_heuristic"
_C = math.sqrt(2.0)


def _evidence(hit: LessonHit) -> int | None:
    raw = hit.metadata.get("evidence_count")
    if isinstance(raw, bool):  # bool is an int subclass — reject
        return None
    return raw if isinstance(raw, int) and raw > 0 else None


def rank_lessons(hits: list[LessonHit]) -> list[LessonHit]:
    """Return hits with heuristic hits UCB-ranked first, others appended in order."""
    heuristics = [h for h in hits if h.source_type == _HEURISTIC_SOURCE]
    others = [h for h in hits if h.source_type != _HEURISTIC_SOURCE]
    if len(heuristics) <= 1:
        return [*heuristics, *others]
    total_n = max(math.e, float(sum(_evidence(h) or 0 for h in heuristics)))
    ln_n = math.log(total_n)

    def score(h: LessonHit) -> float:
        ev = _evidence(h)
        if ev is None:
            return h.similarity
        return h.similarity + _C * math.sqrt(ln_n / ev)

    # Stable sort by descending score (Python sort is stable → ties keep input order).
    ranked = sorted(heuristics, key=score, reverse=True)
    log.engine.debug(
        "[learning] rank_lessons: ranked heuristics",
        extra={"_fields": {"n_heuristic": len(ranked), "n_other": len(others),
                            "top_ref": ranked[0].source_ref if ranked else None}},
    )
    return [*ranked, *others]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/learning/test_heuristic_ranking.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/learning/heuristic_ranking.py tests/learning/test_heuristic_ranking.py
git commit -m "feat(v2): UCB-style rank_lessons — confidence-aware heuristic ordering"
```

---

## Task 3: Miner stamps `mean_quality` into heuristic lesson metadata

**Files:**
- Modify: `src/stackowl/learning/tool_outcome_miner.py` (the `LessonDraft(...)` build, ~line 149-159)
- Test: `tests/learning/test_tool_outcome_miner.py` (add one test; create file if absent)

- [ ] **Step 1: Write the failing test**

First inspect the existing miner test for the construction/fixtures pattern:
Run: `ls tests/learning/ && grep -rn "ToolOutcomeMiner\|def test_" tests/learning/test_tool_outcome_miner.py 2>/dev/null | head`

Add a test asserting the published heuristic lesson carries `mean_quality` in metadata. Mirror the existing miner test's setup (it seeds `task_outcomes` rows + a fake/real lessons index that captures published drafts). Concretely, assert against the captured `LessonDraft.metadata`:

```python
# tests/learning/test_tool_outcome_miner.py  (add)
async def test_published_heuristic_lesson_metadata_includes_mean_quality(miner_env):
    # miner_env: existing fixture that seeds >=3 failing outcomes for one (tool, failure_class)
    # and exposes a captured list of published LessonDrafts. Reuse the fixture used by
    # the existing "publishes a lesson" test in this file.
    await miner_env.miner.mine()
    drafts = miner_env.captured_drafts
    assert drafts, "expected at least one heuristic lesson draft"
    md = drafts[0].metadata
    assert "mean_quality" in md            # NEW: ranking input
    assert "evidence_count" in md          # pre-existing
```

> If no miner test/fixture exists, create `miner_env` mirroring the seeding done by `ToolOutcomeMinerHandler` tests (seed `task_outcomes` via `TaskOutcomeStore`, build `ToolOutcomeMiner(db=..., lessons_index=<capturing fake>)`). The capturing fake records `publish_many`'s argument.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/learning/test_tool_outcome_miner.py -q -k mean_quality`
Expected: FAIL — `assert "mean_quality" in md` (KeyError/AssertionError)

- [ ] **Step 3: Write minimal implementation**

Edit `src/stackowl/learning/tool_outcome_miner.py` — add `mean_quality` to the metadata dict (the value is already computed as `mean_quality` in the same scope; confirm the local variable name when editing):

```python
            new_lessons.append(LessonDraft(
                source_type=_HEURISTIC_LESSON_SOURCE,
                source_ref=str(heuristic_id),
                content=heuristic_summary(mocked),
                metadata={
                    "tool_name": tool_name,
                    "predicted_outcome": predicted_outcome,
                    "failure_class": failure_label,
                    "evidence_count": len(members),
                    "mean_quality": mean_quality,   # NEW — ranking input (Task 2)
                },
            ))
```

> Verify `mean_quality` is in scope at this point (the miner computes it for the heuristic upsert ~line 121-124). If the local name differs, use that name; do NOT recompute.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/learning/test_tool_outcome_miner.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/learning/tool_outcome_miner.py tests/learning/test_tool_outcome_miner.py
git commit -m "feat(v2): miner stamps mean_quality into heuristic lesson metadata (ranking input)"
```

---

## Task 4: Rank + id + contract in `_gather_lessons`, and stash surfaced lessons

**Files:**
- Modify: `src/stackowl/pipeline/steps/classify.py` (`_gather_lessons`, lines 330-385)
- Test: `tests/pipeline/test_gather_lessons.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_gather_lessons.py
import pytest

from stackowl.learning.lesson import LessonHit
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.services import StepServices, set_services, reset_services
from stackowl.pipeline.steps.classify import _gather_lessons


class _FakeIndex:
    def __init__(self, hits): self._hits = hits
    async def search(self, query, *, limit=5, source_filter=None): return self._hits[:limit]


@pytest.mark.asyncio
async def test_gather_ranks_assigns_ids_and_stashes_surfaced():
    hits = [
        LessonHit("a", "tool_heuristic", "a", "low-evidence note", 0.60, {"evidence_count": 3}),
        LessonHit("b", "tool_heuristic", "b", "well-proven note", 0.80, {"evidence_count": 50}),
    ]
    services = StepServices(); services.lessons_index = _FakeIndex(hits)  # type: ignore[attr-defined]
    stoken = set_services(services); ltoken = lc.bind()
    try:
        block = await _gather_lessons("some query", limit=3)
        assert "## Cross-Source Lessons" in block
        assert "note_applied_lesson" in block          # the contract line
        assert "[L1]" in block and "[L2]" in block      # turn-local ids
        # well-proven ("b") ranks first → it is L1
        assert block.index("[L1]") < block.index("[L2]")
        assert "well-proven note" in block.split("[L2]")[0]
        surfaced = lc.get_surfaced()
        assert [s.lesson_id for s in surfaced] == ["L1", "L2"]
        assert surfaced[0].content == "well-proven note"
    finally:
        lc.reset(ltoken); reset_services(stoken)
```

> Note: `StepServices` may not allow attribute assignment if frozen — if so, construct it with `lessons_index=` kwarg instead (check `pipeline/services.py`). Adjust the fixture accordingly; the assertions stay the same.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_gather_lessons.py -q`
Expected: FAIL — no `note_applied_lesson` contract line / no `[L1]` ids / `get_surfaced()` empty.

- [ ] **Step 3: Write minimal implementation**

Add imports near the top of `classify.py`:

```python
from stackowl.learning.heuristic_ranking import rank_lessons
from stackowl.pipeline import lesson_context as lc
```

Replace the EXIT/format block of `_gather_lessons` (current lines 369-385, the `non_skill_hits` → `lines`/`result` section) with:

```python
    # Rank heuristic hits by confidence (Task 2), keep others after them.
    ranked = rank_lessons(non_skill_hits)
    # Assign turn-local ids + stash so note_applied_lesson can resolve a citation.
    surfaced: list[lc.SurfacedLesson] = []
    lines = [
        "## Cross-Source Lessons",
        "If a lesson below changed what you did, call note_applied_lesson with its id.",
    ]
    for i, h in enumerate(ranked, start=1):
        lid = f"L{i}"
        snippet = h.content[:300]
        lines.append(f"- [{lid}] **[{h.source_type}]** ({h.similarity:.2f}) {snippet}")
        surfaced.append(lc.SurfacedLesson(
            lesson_id=lid, source_type=h.source_type, content=h.content, similarity=h.similarity,
        ))
    lc.set_surfaced(tuple(surfaced))
    result = "\n".join(lines)
    log.engine.debug(
        "[pipeline] classify._gather_lessons: exit",
        extra={"_fields": {
            "n_hits": len(ranked), "block_len": len(result),
            "top_sim": ranked[0].similarity if ranked else None,
        }},
    )
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_gather_lessons.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/steps/classify.py tests/pipeline/test_gather_lessons.py
git commit -m "feat(v2): _gather_lessons ranks heuristics, assigns L# ids, stashes surfaced lessons"
```

---

## Task 5: `note_applied_lesson` tool + registration

**Files:**
- Create: `src/stackowl/tools/meta/note_applied_lesson.py`
- Modify: `src/stackowl/tools/registry.py` (`with_defaults`)
- Test: `tests/tools/test_note_applied_lesson.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_note_applied_lesson.py
import pytest

from stackowl.pipeline import lesson_context as lc
from stackowl.tools.meta.note_applied_lesson import NoteAppliedLessonTool


@pytest.mark.asyncio
async def test_records_known_lesson_and_returns_success():
    tool = NoteAppliedLessonTool()
    token = lc.bind()
    try:
        lc.set_surfaced((lc.SurfacedLesson("L1", "tool_heuristic", "browse fails on pdf", 0.9),))
        res = await tool.execute(lesson_id="L1", what_you_did="used fetch instead of browse")
        assert res.success is True
        applied = lc.drain_applied()
        assert len(applied) == 1 and applied[0].lesson_id == "L1"
        assert applied[0].what_you_did == "used fetch instead of browse"
    finally:
        lc.reset(token)


@pytest.mark.asyncio
async def test_unknown_id_still_succeeds_no_raise():
    tool = NoteAppliedLessonTool()
    token = lc.bind()
    try:
        lc.set_surfaced(())
        res = await tool.execute(lesson_id="L9", what_you_did="did a thing")
        assert res.success is True
        assert lc.drain_applied()[0].lesson_summary is None
    finally:
        lc.reset(token)


@pytest.mark.asyncio
async def test_missing_what_you_did_is_rejected_cleanly():
    tool = NoteAppliedLessonTool()
    token = lc.bind()
    try:
        res = await tool.execute(lesson_id="L1", what_you_did="")
        assert res.success is False and res.error
    finally:
        lc.reset(token)


def test_tool_registered_in_defaults():
    from stackowl.tools.registry import ToolRegistry
    reg = ToolRegistry.with_defaults()
    assert reg.get("note_applied_lesson") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/test_note_applied_lesson.py -q`
Expected: FAIL — `ModuleNotFoundError: ...note_applied_lesson`

- [ ] **Step 3: Write minimal implementation**

```python
# src/stackowl/tools/meta/note_applied_lesson.py
"""note_applied_lesson — the model's honest self-report that a surfaced lesson
changed what it did this turn (pillar ④ explainability).

Non-consequential: it has NO side effect beyond recording an in-turn note (no
consent gate). The render step (``surface_applied_lessons``) turns recorded notes
into one user-facing line — ONLY when this tool was called, so the assistant can
never claim a lesson it didn't act on.
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.pipeline import lesson_context as lc
from stackowl.tools.base import Tool, ToolResult

__all__ = ["NoteAppliedLessonTool"]

_SELF_NAME = "note_applied_lesson"


class NoteAppliedLessonTool(Tool):
    """Record that a surfaced lesson (cited by its id) shaped this turn."""

    @property
    def name(self) -> str:
        return _SELF_NAME

    @property
    def description(self) -> str:
        return (
            "Record that one of the lessons listed under '## Cross-Source Lessons' "
            "actually changed what you did THIS turn. Pass its id (e.g. 'L1') and a "
            "short, truthful note of what you did differently because of it. Call it "
            "ONLY when a lesson genuinely influenced your actions — never speculatively. "
            "It has no side effects; it lets the assistant tell the user what it drew on."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "lesson_id": {
                    "type": "string",
                    "description": "The id of the lesson you acted on, e.g. 'L1'.",
                },
                "what_you_did": {
                    "type": "string",
                    "description": "Short truthful note of what you did because of it.",
                },
            },
            "required": ["lesson_id", "what_you_did"],
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        lesson_id = str(kwargs.get("lesson_id", "")).strip()
        what_you_did = str(kwargs.get("what_you_did", "")).strip()
        # 1. ENTRY
        log.tool.debug(
            "note_applied_lesson.execute: entry",
            extra={"_fields": {"lesson_id": lesson_id, "what_len": len(what_you_did)}},
        )
        # 2. DECISION — reject empties cleanly (structured, never raise)
        if not lesson_id or not what_you_did:
            log.tool.info("note_applied_lesson.execute: missing args")
            return ToolResult(
                success=False, output="",
                error="Both 'lesson_id' and 'what_you_did' are required.",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        # 3. STEP — record into the turn-scoped sink
        matched = lc.record_applied(lesson_id, what_you_did)
        # 4. EXIT
        log.tool.info(
            "note_applied_lesson.execute: exit",
            extra={"_fields": {"lesson_id": lesson_id, "matched": matched is not None}},
        )
        return ToolResult(
            success=True,
            output=f"Recorded that lesson {lesson_id} informed this turn.",
            duration_ms=(time.monotonic() - t0) * 1000,
        )
```

Register it in `src/stackowl/tools/registry.py` `with_defaults()`. Add the import near the other meta-tool imports and the registration near `ToolDescribeTool()`:

```python
        from stackowl.tools.meta.note_applied_lesson import NoteAppliedLessonTool
```
```python
        registry.register(ToolDescribeTool())
        # note_applied_lesson — non-consequential self-report so the assistant can
        # honestly tell the user when a learned lesson shaped the turn (pillar ④).
        registry.register(NoteAppliedLessonTool())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tools/test_note_applied_lesson.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/tools/meta/note_applied_lesson.py src/stackowl/tools/registry.py tests/tools/test_note_applied_lesson.py
git commit -m "feat(v2): note_applied_lesson meta-tool — honest model self-report of applied lessons"
```

---

## Task 6: `surface_applied_lessons` render step + localized template

**Files:**
- Modify: `src/stackowl/setup/localize.py` (`_STRINGS`)
- Create: `src/stackowl/pipeline/applied_lessons.py`
- Test: `tests/pipeline/test_applied_lessons_render.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_applied_lessons_render.py
import pytest

from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.applied_lessons import surface_applied_lessons
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(*, responses):
    return PipelineState(
        trace_id="t", session_id="s", input_text="hi", channel="cli",
        owl_name="o", pipeline_step="deliver", responses=responses,
    )


def _answer_chunk(text="here is your answer", is_floor=False):
    return ResponseChunk(content=text, is_final=False, chunk_index=0,
                         trace_id="t", owl_name="o", is_floor=is_floor)


@pytest.mark.asyncio
async def test_appends_one_line_when_applied_and_real_answer():
    token = lc.bind()
    try:
        lc.set_surfaced((lc.SurfacedLesson("L1", "tool_heuristic", "x", 0.9),))
        lc.record_applied("L1", "used fetch instead of browse")
        out = await surface_applied_lessons(_state(responses=(_answer_chunk(),)))
        assert len(out.responses) == 2
        assert "used fetch instead of browse" in out.responses[-1].content
    finally:
        lc.reset(token)


@pytest.mark.asyncio
async def test_no_applied_means_unchanged():
    token = lc.bind()
    try:
        s = _state(responses=(_answer_chunk(),))
        out = await surface_applied_lessons(s)
        assert out.responses == s.responses
    finally:
        lc.reset(token)


@pytest.mark.asyncio
async def test_floor_only_response_gets_no_annotation():
    token = lc.bind()
    try:
        lc.set_surfaced((lc.SurfacedLesson("L1", "tool_heuristic", "x", 0.9),))
        lc.record_applied("L1", "did something")
        s = _state(responses=(_answer_chunk("I couldn't finish", is_floor=True),))
        out = await surface_applied_lessons(s)
        assert out.responses == s.responses          # no extra chunk on a floor-only turn
    finally:
        lc.reset(token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_applied_lessons_render.py -q`
Expected: FAIL — `ModuleNotFoundError: ...applied_lessons`

- [ ] **Step 3: Write minimal implementation**

Add the localized key to `_STRINGS` in `src/stackowl/setup/localize.py` (next to the other `self_heal_*` keys):

```python
    # Applied-lesson explainability (pillar ④) — appended after a real answer when
    # the model self-reported via note_applied_lesson. 1 slot: {what_you_did}.
    ("self_heal_applied_lesson", "en"): "ℹ️ I drew on something I learned: {what_you_did}",
    ("self_heal_applied_lesson", "de"): "ℹ️ Ich habe Gelerntes genutzt: {what_you_did}",
    ("self_heal_applied_lesson", "fr"): "ℹ️ Je me suis appuyé sur un acquis : {what_you_did}",
```

```python
# src/stackowl/pipeline/applied_lessons.py
"""surface_applied_lessons — pre-delivery render of the model's applied-lesson
self-reports (pillar ④). Sibling to ``surface_critical_failure``: runs once per
turn, before deliver, in BOTH backends — so the explanation reaches every channel
with no per-channel duplication.

Honesty: appends a line ONLY when (a) the model called ``note_applied_lesson``
this turn AND (b) there is a real (non-floor) answer to annotate. Never raises.
"""

from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.setup.localize import localize_format

_MAX_LINES = 2
_LANG = "en"  # turn language plumbing is out of scope; localize falls back to en


async def surface_applied_lessons(state: PipelineState) -> PipelineState:
    """Append one localized line per applied lesson (capped). Self-healing."""
    try:
        applied = lc.drain_applied()
        if not applied:
            return state
        # Only annotate a genuine answer — never a floor-only / empty response.
        has_real_answer = any(
            c.content.strip() and not c.is_floor for c in state.responses
        )
        if not has_real_answer:
            log.engine.debug(
                "[applied_lessons] skip — no real answer to annotate",
                extra={"_fields": {"trace_id": state.trace_id, "n_applied": len(applied)}},
            )
            return state
        new_chunks: list[ResponseChunk] = []
        base_index = len(state.responses)
        for offset, a in enumerate(applied[:_MAX_LINES]):
            text = localize_format("self_heal_applied_lesson", _LANG, what_you_did=a.what_you_did)
            new_chunks.append(ResponseChunk(
                content=text, is_final=False, chunk_index=base_index + offset,
                trace_id=state.trace_id, owl_name=state.owl_name,
            ))
        log.engine.info(
            "[applied_lessons] surfaced applied-lesson lines",
            extra={"_fields": {"trace_id": state.trace_id, "n": len(new_chunks)}},
        )
        return state.evolve(responses=(*state.responses, *new_chunks))
    except Exception as exc:  # B5 — never break delivery
        log.engine.error(
            "[applied_lessons] surfacing failed — leaving response untouched",
            exc_info=exc, extra={"_fields": {"trace_id": state.trace_id}},
        )
        return state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pipeline/test_applied_lessons_render.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/setup/localize.py src/stackowl/pipeline/applied_lessons.py tests/pipeline/test_applied_lessons_render.py
git commit -m "feat(v2): surface_applied_lessons render step + localized template (pillar 4)"
```

---

## Task 7: Wire both backends (bind/reset + call render) — driven by the journey happy-path

**Files:**
- Modify: `src/stackowl/pipeline/backends/asyncio_backend.py`
- Modify: `src/stackowl/pipeline/backends/langgraph_backend.py`
- Test: `tests/journeys/test_learning_explainability_journey.py` (create; happy path first)

- [ ] **Step 1: Write the failing test (happy path FR2)**

Use the scripted-provider-via-`complete_with_tools` pattern from `tests/journeys/test_j4_tools_bounds.py`. The provider, within ONE `complete_with_tools` call, drives the REAL tool loop: it calls `note_applied_lesson` through `tool_dispatcher`, then returns a final reply. Boot the same gateway/pipeline harness those journeys use (copy the construction from `test_j4_tools_bounds.py` / `test_self_heal_substitution.py`).

```python
# tests/journeys/test_learning_explainability_journey.py
import pytest

# Reuse the journey harness helpers from the existing journeys (gateway + pipeline
# construction). Mirror test_j4_tools_bounds.py exactly for boot; only the scripted
# provider body changes.

_FINAL = "Here is the summary you asked for."


class _ScriptedLessonOwl:
    """The ONLY mock. Calls note_applied_lesson via the real dispatcher, then answers."""
    protocol = "anthropic"

    @property
    def name(self) -> str:
        return "lesson_owl"

    async def complete_with_tools(self, *, user_text, system_text, tool_schemas,
                                   tool_dispatcher, history=None, **_kw):
        # Cite L1 (the harness seeds a surfaced lesson with that id — see fixture).
        await tool_dispatcher("note_applied_lesson",
                              {"lesson_id": "L1", "what_you_did": "used fetch instead of browse_url"})
        return (_FINAL, [])

    async def complete(self, messages, model, **kwargs):
        from stackowl.providers.base import CompletionResult
        return CompletionResult(content="ok", input_tokens=1, output_tokens=1,
                                model="m", provider_name="lesson_owl", duration_ms=1.0)

    async def stream(self, *a, **k):
        if False:
            yield ""


@pytest.mark.asyncio
async def test_applied_lesson_is_explained_to_user(journey_env):
    # journey_env seeds a surfaced lesson addressable as "L1" for this turn.
    # The simplest seam: pre-bind lesson_context and set_surfaced before the turn,
    # OR seed the lessons index so classify surfaces it as L1. Prefer seeding the
    # lessons index (end-to-end) — see fixture note below.
    reply = await journey_env.send("summarize this page", provider=_ScriptedLessonOwl())
    assert _FINAL in reply
    assert "used fetch instead of browse_url" in reply   # FR2 — visible explanation
```

> **Fixture note (`journey_env`):** model it on the harness in `test_j4_tools_bounds.py`. To make `L1` resolvable end-to-end, seed the real `LessonsIndex` with one `tool_heuristic` lesson before the turn so `_gather_lessons` surfaces it as `L1` (requires the embedder; if the journey harness runs without an embedder, instead assert the explanation via a provider that also exercises a seeded surfaced lesson — see Task 8 for the embedder-free variant). The `send(...)` helper returns the concatenated user-visible response text.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/journeys/test_learning_explainability_journey.py -q`
Expected: FAIL — the explanation line is absent (render step not wired into the backend).

- [ ] **Step 3: Wire `asyncio_backend.py`**

Add the import:
```python
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.applied_lessons import surface_applied_lessons
```

Bind right after `trace_token = TraceContext.start(...)` (line ~55):
```python
        lesson_token = lc.bind()
```

Call the render step right after `surface_critical_failure(...)` (line ~83), before the `pipeline_step="deliver"` evolve:
```python
            current = await surface_critical_failure(current, self._services)
            current = await surface_applied_lessons(current)
```

Reset in the existing `finally:` (where `reset_services(token)` / `TraceContext` reset live — find the finally that closes the `try` opened at line ~58):
```python
        finally:
            lc.reset(lesson_token)
            # ... existing reset_services / trace reset ...
```

> Confirm the existing `finally` block resets services/trace; add `lc.reset(lesson_token)` as the first line of it so it always runs.

- [ ] **Step 4: Run test to verify it passes (asyncio backend)**

Run: `uv run pytest tests/journeys/test_learning_explainability_journey.py -q`
Expected: PASS (if the journey harness uses AsyncioBackend).

- [ ] **Step 5: Wire `langgraph_backend.py` (parity)**

Add imports:
```python
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.applied_lessons import surface_applied_lessons
```

Extend `_deliver_with_surfacing` (lines 35-43):
```python
async def _deliver_with_surfacing(state: PipelineState) -> PipelineState:
    surfaced = await surface_critical_failure(state, get_services())
    surfaced = await surface_applied_lessons(surfaced)
    return await deliver.run(surfaced)
```

In `LangGraphBackend.run(...)`, bind after the `TraceContext.start(...)` call and reset in its `finally` (mirror the asyncio change — locate the `set_services`/`TraceContext` setup + `finally` in this file's `run`):
```python
        lesson_token = lc.bind()
        try:
            ...
        finally:
            lc.reset(lesson_token)
            ...
```

- [ ] **Step 6: Run targeted regression on both backends**

Run: `uv run pytest tests/journeys/test_learning_explainability_journey.py tests/journeys/test_self_heal_substitution.py tests/journeys/test_j4_tools_bounds.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/pipeline/backends/asyncio_backend.py src/stackowl/pipeline/backends/langgraph_backend.py tests/journeys/test_learning_explainability_journey.py
git commit -m "feat(v2): wire surface_applied_lessons + lesson_context bind into both backends"
```

---

## Task 8: Journey negatives (FR3/FR4/FR5) + ranking (FR1) + full regression (FR6)

**Files:**
- Modify: `tests/journeys/test_learning_explainability_journey.py` (add negative + ranking scenarios)

- [ ] **Step 1: Write the failing tests**

Add to the journey file (each scripts a different provider behavior; reuse `journey_env`):

```python
class _SilentOwl:
    """Answers WITHOUT calling note_applied_lesson — must produce no claim."""
    protocol = "anthropic"
    @property
    def name(self): return "silent_owl"
    async def complete_with_tools(self, *, tool_dispatcher, **_kw):
        return ("Plain answer, no lesson used.", [])
    async def complete(self, messages, model, **kwargs):
        from stackowl.providers.base import CompletionResult
        return CompletionResult(content="ok", input_tokens=1, output_tokens=1,
                                model="m", provider_name="silent_owl", duration_ms=1.0)
    async def stream(self, *a, **k):
        if False: yield ""


@pytest.mark.asyncio
async def test_no_claim_when_tool_not_called(journey_env):       # FR3
    reply = await journey_env.send("do a thing", provider=_SilentOwl())
    assert "Plain answer" in reply
    assert "I drew on something I learned" not in reply
    assert "ℹ️" not in reply


class _BogusIdOwl(_ScriptedLessonOwl):
    @property
    def name(self): return "bogus_owl"
    async def complete_with_tools(self, *, tool_dispatcher, **_kw):
        await tool_dispatcher("note_applied_lesson",
                              {"lesson_id": "L99", "what_you_did": "applied a vague intuition"})
        return ("Answer with a bogus citation.", [])


@pytest.mark.asyncio
async def test_unknown_id_does_not_error_and_uses_what_you_did(journey_env):   # FR4
    reply = await journey_env.send("do a thing", provider=_BogusIdOwl())
    assert "Answer with a bogus citation." in reply
    assert "applied a vague intuition" in reply     # explanation from what_you_did only
```

For **FR1 (ranking, end-to-end)** add an assertion-on-the-block test if the harness exposes the built system prompt; otherwise rely on the Task 2 unit + a `_gather_lessons` integration assertion (Task 4 test already covers ranking order in the injected block). For **FR5 (floor turn → no annotation)** the Task 6 unit already covers the render contract; add a journey variant only if the harness can easily script a zero-provider floor.

> Keep negative scenarios that genuinely need the gateway here; ranking/floor unit coverage already exists (Tasks 2/4/6). Do not duplicate — note in the test docstring which FR each covers.

- [ ] **Step 2: Run to verify they fail (then pass after no code change — these assert already-correct behavior)**

Run: `uv run pytest tests/journeys/test_learning_explainability_journey.py -q`
Expected: The negatives should PASS immediately if Task 7 is correct (they assert the honesty invariants). If `test_no_claim_when_tool_not_called` FAILS (a claim leaked), that is a real defect — STOP and inspect `surface_applied_lessons` / bind-reset isolation before continuing (do NOT weaken the test).

- [ ] **Step 3: Full journey regression (FR6)**

Run: `timeout 600 uv run pytest -q -p no:cacheprovider tests/journeys/`
Expected: PASS — 82 passed, 1 skipped (the prior 81 + the new journey file), ~90s. **Zero regression is the gate.**

- [ ] **Step 4: Lint + type-check the slice**

Run:
```bash
uv run ruff check src/stackowl/pipeline/lesson_context.py src/stackowl/pipeline/applied_lessons.py src/stackowl/learning/heuristic_ranking.py src/stackowl/tools/meta/note_applied_lesson.py
uv run mypy src/stackowl/pipeline/lesson_context.py src/stackowl/pipeline/applied_lessons.py src/stackowl/learning/heuristic_ranking.py src/stackowl/tools/meta/note_applied_lesson.py
```
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add tests/journeys/test_learning_explainability_journey.py
git commit -m "test(v2): learning-explainability journey negatives — no overclaim, graceful id mismatch"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** FR1→Task 2+4; FR2→Task 5+6+7; FR3→Task 8; FR4→Task 5+8; FR5→Task 6; FR6→Task 8. Honesty invariants 1-5 → lesson_context unbound-no-op + render real-answer guard + tool no-raise + B5 catches. Ranking inputs → Task 3. All covered.
- **Placeholder scan:** No TBD/TODO. The two "fixture note" callouts are explicit harness-reuse instructions pointing at named existing files, not deferred work.
- **Type consistency:** `SurfacedLesson`/`AppliedLesson` fields, `rank_lessons(list[LessonHit]) -> list[LessonHit]`, `record_applied(str,str)->SurfacedLesson|None`, `drain_applied()->tuple[AppliedLesson,...]`, `surface_applied_lessons(state)->state` consistent across all tasks. Tool name `note_applied_lesson` consistent. Localize key `self_heal_applied_lesson` consistent.

## Risk & containment
- **Risk:** weak local model never calls the tool → no explanation. **Contained:** by design (fail-safe to silence); not a regression.
- **Risk:** ContextVar leaks across concurrent turns. **Contained:** bind/reset in both backends' `finally`; each turn is its own async task (ContextVars are per-context). Negative journey FR3 would catch a cross-turn leak.
- **Risk:** journey harness lacks an embedder so `L1` isn't surfaced end-to-end. **Contained:** the Task 7 fixture note gives an embedder-free seam (pre-bind + set_surfaced) as fallback; ranking/injection already unit-covered.
- **Rollback:** pure-additive (see spec); revert the 4 new files + the backend/classify/miner/localize/registry edits.
