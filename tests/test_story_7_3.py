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
)
from stackowl.brief.models import BriefSection, MorningBrief
from stackowl.brief.renderer import BriefRenderer
from tests._story_7_3_helpers import (
    StubDb,
    StubScheduler,
    make_ctx,
    make_job,
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
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


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
