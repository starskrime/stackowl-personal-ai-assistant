"""Story 7.3 — Morning brief multi-section format + all sources (part A).

Tests in this file cover the pure-data side:

* :class:`BriefSection` / :class:`MorningBrief` immutability + ``extra="forbid"``
* :class:`BriefRenderer.render()` formatting (separators, key headers, omit)
* The four assemblers' happy + degenerate paths

Handler orchestration, command surface, settings + guard tests live in
:mod:`tests.test_story_7_3b` to keep each file under the B2 300-line cap.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.brief.assemblers import (
    AgentStatusAssembler,
    DateAndPrioritiesAssembler,
    MemoryHighlightsAssembler,
    PendingStagedFactsAssembler,
)
from stackowl.brief.models import BriefSection, MorningBrief
from stackowl.brief.renderer import BriefRenderer
from tests._story_7_3_helpers import (
    StubDb,
    StubMemory,
    StubScheduler,
    make_ctx,
    make_job,
    make_record,
    make_staged,
)

# ---------------------------------------------------------------------------
# 1–2. Models are frozen / extra-forbid
# ---------------------------------------------------------------------------


def test_brief_section_is_frozen_and_forbid_extra() -> None:
    sec = BriefSection(key="k", title="K", items=["a"])
    with pytest.raises(ValidationError):
        sec.key = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        BriefSection(key="k", title="K", items=[], unknown="x")  # type: ignore[call-arg]


def test_morning_brief_is_frozen_and_forbid_extra() -> None:
    brief = MorningBrief(
        sections=[BriefSection(key="a", title="A")],
        generated_at="2026-05-23T00:00:00+00:00",
        delivery_channels=["cli"],
    )
    with pytest.raises(ValidationError):
        brief.generated_at = "x"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        MorningBrief(  # type: ignore[call-arg]
            sections=[],
            generated_at="t",
            delivery_channels=[],
            extra_field=1,
        )


# ---------------------------------------------------------------------------
# 3–5. Renderer
# ---------------------------------------------------------------------------


def test_render_omits_sections_marked_omitted() -> None:
    brief = MorningBrief(
        sections=[
            BriefSection(key="a", title="A", items=["x"]),
            BriefSection(key="b", title="B", items=[], omitted=True),
            BriefSection(key="c", title="C", items=["y"]),
        ],
        generated_at="t",
        delivery_channels=["cli"],
    )
    out = BriefRenderer().render(brief)
    assert "A" in out and "C" in out
    # B header never rendered as its own line
    assert "\nB\n" not in out
    # only two separators for two visible sections
    assert out.count(BriefRenderer.SEPARATOR) == 2


def test_render_inserts_separator_between_sections() -> None:
    brief = MorningBrief(
        sections=[
            BriefSection(key="a", title="A", items=["x"]),
            BriefSection(key="b", title="B", items=["y"]),
        ],
        generated_at="t",
        delivery_channels=["cli"],
    )
    out = BriefRenderer().render(brief)
    sep = BriefRenderer.SEPARATOR
    assert sep in out
    assert out.count(sep) == 2
    assert "  x" in out and "  y" in out


def test_render_uses_section_key_as_header_no_english_literal() -> None:
    brief = MorningBrief(
        sections=[BriefSection(key="custom_key", title="ignored", items=["v"])],
        generated_at="t",
        delivery_channels=["cli"],
    )
    out = BriefRenderer().render(brief)
    # Header is the uppercased key — not the (potentially English) title.
    assert "CUSTOM_KEY" in out
    # No accidental English section labels leaked from the renderer itself.
    for forbidden in ("Date", "Today", "Memory", "Agents", "Section"):
        assert forbidden not in out


# ---------------------------------------------------------------------------
# 6. DateAndPrioritiesAssembler returns non-empty items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_date_and_priorities_assemble_returns_non_empty() -> None:
    db = StubDb(
        fetch_responses={
            "FROM jobs": [
                {"job_id": "g-1", "schedule": "daily@09:00"},
                {"job_id": "g-2", "schedule": "hourly"},
            ]
        }
    )
    assembler = DateAndPrioritiesAssembler(db=db)  # type: ignore[arg-type]
    section = await assembler.assemble(make_ctx())
    assert section.key == "date_and_priorities"
    assert section.omitted is False
    assert len(section.items) == 3  # 1 timestamp + 2 goal rows
    assert section.items[0].startswith("now:")
    assert any("g-1" in i for i in section.items)


# ---------------------------------------------------------------------------
# 7–8. MemoryHighlightsAssembler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_highlights_surfaces_nothing_notable_when_recall_empty() -> None:
    """F-79: an empty recall must NOT be silently omitted.

    A chronically-empty highlights section would otherwise vanish without a
    trace; instead the brief surfaces an explicit "nothing notable" item so the
    user (and an operator reading the rendered brief) can SEE the section ran and
    found nothing — the section renders (``omitted=False``).
    """
    from stackowl.brief.assemblers import _NOTHING_NOTABLE_ITEM

    mem = StubMemory(records=[])
    section = await MemoryHighlightsAssembler(memory_bridge=mem).assemble(make_ctx())
    assert section.omitted is False
    assert section.items == [_NOTHING_NOTABLE_ITEM]
    # Over-fetches a wider candidate pool (20) so the client-side 24h recency
    # filter still has enough real candidates to fill 3 highlights from.
    assert mem.recall_calls and mem.recall_calls[0][1] == 20


@pytest.mark.asyncio
async def test_memory_highlights_logs_empty_recall_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """F-79: empty recall is logged at INFO (not debug) so a chronically-empty
    highlights section is visible without enabling debug logging."""
    import logging

    mem = StubMemory(records=[])
    with caplog.at_level(logging.INFO, logger="stackowl.scheduler"):
        await MemoryHighlightsAssembler(memory_bridge=mem).assemble(make_ctx())
    assert any(
        record.levelno == logging.INFO and "no records" in record.getMessage()
        for record in caplog.records
    ), "empty recall must emit an INFO-level record"


@pytest.mark.asyncio
async def test_memory_highlights_returns_items_when_records_exist() -> None:
    long_content = "x" * 200
    mem = StubMemory(
        records=[
            make_record("first fact"),
            make_record(long_content),
            make_record("third"),
        ]
    )
    section = await MemoryHighlightsAssembler(memory_bridge=mem).assemble(make_ctx())
    assert section.omitted is False
    assert len(section.items) == 3
    # 120-char truncation enforced on long entries
    assert len(section.items[1]) == 120


@pytest.mark.asyncio
async def test_memory_highlights_excludes_stale_records_outside_24h_window() -> None:
    """Live incident: recall() is pure semantic top-K with no recency filter, so
    a vague query ("recent important facts") can rank an old, generically
    "important"-sounding fact above anything from today — stale world-news
    content from an earlier session kept resurfacing in every brief, described
    by the user as "not my memory". This class's own docstring promises "last
    24h"; that must actually be enforced, client-side since recall() has no
    time-window parameter."""
    from datetime import UTC, datetime, timedelta

    from stackowl.memory.models import MemoryRecord

    def _record_at(content: str, age: timedelta) -> MemoryRecord:
        return MemoryRecord(
            fact_id=content,
            content=content,
            embedding=[0.0, 0.0],
            embedding_model="stub",
            committed_at=datetime.now(UTC) - age,
            source_type="conversation",
            source_ref="test",
        )

    mem = StubMemory(
        records=[
            _record_at("fresh fact", timedelta(hours=1)),
            _record_at("stale gaza news", timedelta(days=30)),
            _record_at("another stale one", timedelta(days=10)),
        ]
    )
    section = await MemoryHighlightsAssembler(memory_bridge=mem).assemble(make_ctx())
    assert section.items == ["fresh fact"]


# ---------------------------------------------------------------------------
# 9–10. PendingStagedFactsAssembler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_staged_omitted_when_zero() -> None:
    mem = StubMemory(staged=[])
    section = await PendingStagedFactsAssembler(memory_bridge=mem).assemble(make_ctx())
    assert section.omitted is True
    assert section.items == []


@pytest.mark.asyncio
async def test_pending_staged_returns_count_when_facts_exist() -> None:
    mem = StubMemory(
        staged=[make_staged(), make_staged(), make_staged("committed")]
    )
    section = await PendingStagedFactsAssembler(memory_bridge=mem).assemble(make_ctx())
    assert section.omitted is False
    assert section.items == ["staged_count:2"]


# ---------------------------------------------------------------------------
# 11. AgentStatusAssembler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_status_returns_status_counts() -> None:
    jobs = [
        make_job(handler="goal_execution", status="pending", enabled=True),
        make_job(handler="goal_execution", status="pending", enabled=True),
        make_job(handler="goal_execution", status="pending", enabled=False),  # paused
        make_job(handler="goal_execution", status="failed", enabled=True),
    ]
    sched = StubScheduler(jobs=jobs)
    section = await AgentStatusAssembler(scheduler=sched).assemble(make_ctx())  # type: ignore[arg-type]
    assert section.omitted is False
    assert section.items == ["scheduled:2", "paused:1", "failed:1"]
