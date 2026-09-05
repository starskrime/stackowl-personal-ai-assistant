"""The platform cannot see its own largest cost, and that cost silently shortens turns.

MEASURED 2026-09-05. Every provider round re-sends an unchanging prefix — 79 tool
schemas (~19,900 tokens) plus the system prompt — and across the traced half of
`cost_records` **384,429,704 tokens, 64% of the NeraAiRaw bill, is prefix already
sent earlier in the same turn**. Nothing in the platform measures that:
`grep -rn input_tokens src/stackowl/health/` returned ZERO before this contributor.

WHY GROWTH RATHER THAN SHARE. Reporting "64% of your tokens are re-sent prefix" would
be a permanently-degraded alarm — CLAUDE.md defect shape #4, no decay — and an alarm
that can never clear is one the operator learns to ignore. The share is a property of
the architecture; the GROWTH is a regression, and it is the one that bites.

WHAT GROWTH ACTUALLY COSTS, and it is not tokens. A turn dies when its cumulative
token cap (500,000) is reached. At the measured median prefix-carrying round of
24,811 tokens that happens at ~20.15 rounds — sitting almost exactly on the 20-step
cap by coincidence, not design. Add twenty tools at the measured ~249 tokens each and
the median goes to ~29,800, the crossing falls to ~16.8 rounds, and **every turn on
the platform silently gets a shorter leash with nothing announcing it**. That is the
trajectory this contributor exists to catch: the tool registry only grows, and the
one backstop (`HARD_TOOL_COUNT_CAP = 150` against 79 registered) permits roughly
double today's prefix without ever evicting anything.

DEGRADED, NEVER DOWN, and owner-scoped because `cost_records` is owner-governed —
`tests/tenancy/test_no_owner_scope_bypass.py` exists because that predicate was
omitted once already.
"""

from __future__ import annotations

import pytest

from stackowl.health.contributors import PrefixGrowthContributor


class _Rows:
    """Stub pool: returns the two windows the contributor asks for, in order."""

    def __init__(self, recent: list[int], baseline: list[int]) -> None:
        self._answers = [recent, baseline]
        self.sql_seen: list[str] = []

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:  # noqa: ARG002
        self.sql_seen.append(sql)
        rows = self._answers.pop(0) if self._answers else []
        return [{"input_tokens": n} for n in rows]


@pytest.mark.asyncio
async def test_a_flat_prefix_is_healthy() -> None:
    pool = _Rows(recent=[24_000] * 60, baseline=[24_000] * 60)
    status = await PrefixGrowthContributor(pool).health_check()
    assert status.status == "ok"


@pytest.mark.asyncio
async def test_a_GROWING_prefix_is_degraded_and_says_by_how_much() -> None:
    """The measured trajectory: +20 tools moves the median ~24.8k -> ~29.8k."""
    pool = _Rows(recent=[29_800] * 60, baseline=[24_800] * 60)
    status = await PrefixGrowthContributor(pool).health_check()

    assert status.status == "degraded"
    assert "24,800" in status.message and "29,800" in status.message, status.message


@pytest.mark.asyncio
async def test_it_reports_the_consequence_not_just_the_number() -> None:
    """A token count means nothing to an operator. "Your turns are now N rounds
    instead of M" is the same fact in the unit they actually feel."""
    pool = _Rows(recent=[29_800] * 60, baseline=[24_800] * 60)
    status = await PrefixGrowthContributor(pool).health_check()

    assert "round" in status.message.lower(), status.message
    # 500,000 / 29,800 = 16.78 -> "~17"; 500,000 / 24,800 = 20.16 -> "~20".
    # This asserted "16" first, from the unrounded quotient — the code was right and
    # the expectation was wrong, which is worth keeping: a test that pins a number it
    # derived by hand rather than the number the code reports is a test that will
    # eventually be "fixed" by breaking working code.
    assert "~17 rounds" in status.message, status.message
    assert "~20" in status.message, status.message


@pytest.mark.asyncio
async def test_a_SHRINKING_prefix_is_not_an_alarm() -> None:
    """Someone trimming the prompt is good news. Reporting it as degraded is how
    a detector teaches people to ignore it."""
    pool = _Rows(recent=[18_000] * 60, baseline=[24_800] * 60)
    assert (await PrefixGrowthContributor(pool).health_check()).status == "ok"


@pytest.mark.asyncio
async def test_too_few_samples_is_UNKNOWN_rather_than_a_ratio() -> None:
    """Two rounds growing is arithmetic, not a measurement — the same
    "0 exemptions over 7 browser calls" shape the sibling contributor guards
    against with its own floor."""
    pool = _Rows(recent=[40_000, 41_000], baseline=[24_000, 24_100])
    status = await PrefixGrowthContributor(pool).health_check()

    assert status.status == "ok"
    assert "not enough" in status.message.lower(), status.message


@pytest.mark.asyncio
async def test_an_empty_history_does_not_divide_by_zero() -> None:
    pool = _Rows(recent=[], baseline=[])
    status = await PrefixGrowthContributor(pool).health_check()
    assert status.status == "ok"


@pytest.mark.asyncio
@pytest.mark.tripwire
async def test_the_query_is_owner_scoped() -> None:
    """`cost_records` is owner-governed. This exact predicate was omitted once
    already in `usage_report.py`, which is why the tenancy tripwire exists."""
    pool = _Rows(recent=[24_000] * 60, baseline=[24_000] * 60)
    await PrefixGrowthContributor(pool).health_check()

    assert pool.sql_seen, "the contributor issued no query at all"
    for sql in pool.sql_seen:
        assert "owner_id" in sql, f"unscoped read of an owner-governed table: {sql}"


@pytest.mark.tripwire
def test_the_contributor_is_actually_CONSTRUCTED_in_production() -> None:
    """Built-but-not-wired is this repo's most expensive recurring defect, and it
    has already caught `ResilienceContributor` (written, tested, never constructed)
    in this same file. A detector nobody builds measures nothing."""
    import pathlib

    assembly = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src" / "stackowl" / "scheduler" / "assembly.py"
    ).read_text(encoding="utf-8")
    assert "PrefixGrowthContributor(" in assembly, (
        "PrefixGrowthContributor is not constructed in the in-process health "
        "assembly — it would report nothing, forever."
    )


@pytest.mark.asyncio
async def test_the_default_window_can_actually_gather_a_verdict() -> None:
    """A contributor that always answers "not enough to judge" is honest and
    useless. One trace contributes ONE sample, so the rate is turns-per-day:
    measured, 24h yielded 25 recent / 5 baseline against a floor of 30."""
    from stackowl.health.contributors import PrefixGrowthContributor as C

    assert C(_Rows([], []))._window_hours >= 72
