"""The numerator was persisted for 131,644 calls and the denominator for none.

WHY THIS EXISTS. D03.2 ("Conversation compression", P1) carried a closing check
that reported `0` — and that `0` could not be read. Zero WHAT? Zero calls near
the window, or zero instrument? The sibling guard
`test_a_call_near_the_window_says_so.py` shipped the WARNING that fires above 50%
of the window, and its own docstring names the shape this file finishes:

    "The data existed in two places — the window in the probe cache, the tokens
    in cost_records — and nobody joined them."

MY FIRST DIAGNOSIS OF THIS WAS WRONG AND THE CORRECTION IS THE POINT. I measured
the LOGS — 8,217 records carrying `input_tokens`, 1,256 carrying `window`, and
the PAIR appearing ZERO times — and concluded the distribution had no record at
all. It has one: `cost_records` holds 131,644 rows, MEASURED 2026-09-08, 7 over
50% of a 262,144 window, 390 over 25%, peak 162,912.

THE REAL ASYMMETRY IS SHARPER THAN "NOBODY JOINED THEM". The NUMERATOR is
persisted on every row. The DENOMINATOR is persisted NOWHERE:
`providers/model_window.py`'s cache is in-process and EMPTY AT REST, so a closing
check running in a fresh shell cannot resolve the window at all. That is why
D03.2's check had to hardcode 262,144, and why the 2026-08-30 measurement that
FALSIFIED D03.2's earlier closure had to assume the same number. Every one of
those percentages is an inference about a value nobody wrote down.

AND THE CAUSE REACHES ONE FUNCTION. `window_pressure` COMPUTED the ratio and
JUDGED it in the same breath, returning None below `_WINDOW_HIGH_WATER` — so the
only code in the tree that computed the fraction DESTROYED it for 131,637 of
131,644 calls, by design. Conflating "what is the measurement" with "is the
measurement alarming" is precisely why a distribution could never be observed:
an exceedance counter can say IF a threshold was crossed and never HOW FULL
things generally run. `window_fraction` is now the measurement and
`window_pressure` ASKS it — one source for the ratio, defect shape 3.

WHY A COLUMN AND NOT A LOG FIELD. This file's own landmine: "a log-reading check
becomes a RECORD when its evidence rotates, and then reads 0 forever". Ten dated
log files are retained. A denominator that lives only on a log line would make
D03.2's check unreadable again in ten days, which is the defect this fixes.
"""

from __future__ import annotations

import logging
import re

import pytest

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.providers.base import _WINDOW_HIGH_WATER, window_fraction, window_pressure
from stackowl.providers.cost_tracker import CostTracker

pytestmark = pytest.mark.asyncio


async def _record(tracker: CostTracker, **over: object) -> None:
    kwargs: dict[str, object] = dict(
        provider_name="NeraAiRaw", model="neraai-v1-raw",
        input_tokens=5_248, output_tokens=15, duration_ms=12.5,
    )
    kwargs.update(over)
    await tracker.record(**kwargs)  # type: ignore[arg-type]


def _seed_window(monkeypatch: pytest.MonkeyPatch, window: int) -> None:
    """Seed the REAL window cache, exactly as `_record_cost` populates it.

    Not a replacement for `cached_window` — a test double that stops resembling
    the real thing is defect shape 2, and this path's whole subject is whether the
    real lookup can be resolved at record time.
    """
    from stackowl.providers import model_window

    monkeypatch.setitem(
        model_window._WINDOW_CACHE, ("NeraAiRaw", "neraai-v1-raw"), window
    )


async def _one_row(db: DbPool) -> dict:
    rows = await db.fetch_all(
        "SELECT input_tokens, cost_usd, context_window FROM cost_records"
    )
    assert len(rows) == 1, f"expected exactly one row, got {len(rows)}"
    return dict(rows[0])


class TestTheMeasurementSurvivesTheJudgement:
    """The regression that matters: an ordinary call now HAS a fraction."""

    def test_an_ordinary_call_has_a_fraction_even_though_it_is_not_alarming(self) -> None:
        # The median call. window_pressure says nothing about it, and used to be
        # the only thing that could speak — which is why 131,637 of 131,644 calls
        # contributed nothing to any distribution.
        assert window_pressure(input_tokens=5_248, window=262_144) is None
        fraction = window_fraction(input_tokens=5_248, window=262_144)
        assert fraction is not None, "the measurement was destroyed by the judgement"
        assert fraction == pytest.approx(5_248 / 262_144)

    def test_the_two_agree_wherever_the_judgement_speaks(self) -> None:
        """One source for the ratio: above the high-water mark they are equal."""
        for tokens in (131_072, 162_912, 262_144):
            assert window_pressure(input_tokens=tokens, window=262_144) == (
                window_fraction(input_tokens=tokens, window=262_144)
            )

    def test_an_unresolved_window_is_unknown_to_BOTH(self) -> None:
        """None here means "no denominator", never "a small fraction"."""
        for bad in (None, 0, -1):
            assert window_fraction(input_tokens=999_999, window=bad) is None
            assert window_pressure(input_tokens=999_999, window=bad) is None

    def test_the_judgement_threshold_is_unchanged(self) -> None:
        """Splitting the measurement out must not move the watch."""
        assert _WINDOW_HIGH_WATER == 0.5
        assert window_pressure(input_tokens=131_072, window=262_144) is not None
        assert window_pressure(input_tokens=131_071, window=262_144) is None


class TestTheDenominatorIsPersistedBesideTheNumerator:
    async def test_the_window_in_force_is_written_to_the_row(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE FIX. The row can now answer "how full was the window" on its own,
        in a fresh process, after the in-process cache is long gone."""
        _seed_window(monkeypatch, 262_144)
        tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
        await _record(tracker)

        row = await _one_row(tmp_db)
        assert row["context_window"] == 262_144
        assert row["input_tokens"] == 5_248
        # and the ratio the check wants is now computable FROM THE ROW
        assert row["input_tokens"] / row["context_window"] == pytest.approx(0.02, abs=0.01)

    async def test_an_unresolved_window_records_NULL_not_a_guess(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NULL says "unknown". A DEFAULT would manufacture the very 262,144
        assumption this column exists to remove."""
        # NOTHING SEEDED. An empty cache is the real at-rest state — no double at
        # all, which is the whole point: this is what every fresh process sees.
        tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
        await _record(tracker)

        row = await _one_row(tmp_db)
        assert row["context_window"] is None
        assert row["input_tokens"] == 5_248, "a missing denominator lost the numerator"

    async def test_a_lookup_that_raises_never_costs_the_record(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B5. Cost accounting must not break a completion that already happened,
        and a denominator is never worth a call."""
        import stackowl.providers.model_window as mw

        def _boom(*_a: object, **_k: object) -> int:
            raise RuntimeError("probe store unavailable")

        # the ONE case that needs a double: a real cache cannot be made to raise.
        monkeypatch.setattr(mw, "cached_window", _boom)
        tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
        await _record(tracker)

        row = await _one_row(tmp_db)
        assert row["context_window"] is None
        assert row["input_tokens"] == 5_248
        assert row["cost_usd"] is not None


class TestTheCostLineCarriesTheRatio:
    async def test_the_INFO_line_reports_window_and_fraction(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """INFO, because production runs at INFO — a DEBUG line is not evidence."""
        _seed_window(monkeypatch, 262_144)
        tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
        with caplog.at_level(logging.INFO):
            await _record(tracker, input_tokens=131_072)

        cost = [r for r in caplog.records if r.getMessage().startswith("[cost] ")]
        assert cost, "the [cost] line did not fire"
        fields = getattr(cost[-1], "_fields", {})
        assert fields.get("window") == 262_144
        assert fields.get("fraction_of_window") == pytest.approx(0.5)

    async def test_the_fraction_is_not_rounded_away(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The median call is 2% of the window. Rounded to zero places every
        ordinary call reads 0.0 and the distribution is destroyed a second time."""
        _seed_window(monkeypatch, 262_144)
        tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
        with caplog.at_level(logging.INFO):
            await _record(tracker)

        cost = [r for r in caplog.records if r.getMessage().startswith("[cost] ")]
        fields = getattr(cost[-1], "_fields", {})
        assert fields.get("fraction_of_window", 0) > 0.0, "rounded to nothing"


def test_the_insert_names_as_many_columns_as_it_binds() -> None:
    """A parity guard for the shape of bug this change could introduce.

    Adding a column to the list and forgetting its placeholder is silent until a
    call is actually recorded, and this INSERT sits behind a try/except that logs
    and re-raises — so the first symptom is a lost cost row in production.
    """
    import inspect

    import stackowl.providers.cost_tracker as ct

    src = inspect.getsource(ct)
    m = re.search(
        r"INSERT INTO cost_records \((?P<cols>.*?)\)\s*VALUES \((?P<ph>[?,\s]*)\)",
        src, re.S,
    )
    assert m, "the INSERT could not be located — this guard has gone blind"
    cols = [c.strip() for c in m.group("cols").split(",") if c.strip()]
    placeholders = [p for p in m.group("ph").split(",") if p.strip() == "?"]
    assert len(cols) == len(placeholders), (
        f"{len(cols)} columns bound to {len(placeholders)} placeholders: {cols}"
    )
    assert "context_window" in cols
