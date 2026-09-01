"""A capability that fails no more than the platform does is not an incident.

THE COST, MEASURED 2026-09-01. The incident lane spent **19,167,115 tokens in 26
hours** — 64% of ALL platform spend — across 137 distinct incidents averaging
~140,000 tokens each. Bakir's instruction was blunt: "Why my incidents using 15
million thats is a lot and we need to fix it to have 0."

THE PREVIOUS FIX WAS A SYMPTOM FIX, and the platform said so by not healing. A
durable 24h dedup ledger was shipped the night before and it works — the ledger
holds one row per signature and "already diagnosed within 24h" fires. Spend
carried on at 500k–1.7M tokens an HOUR anyway. By this project's own rule (if
the platform does not heal itself after your fix, the core is not fixed), that
is the evidence that de-duplicating the diagnoses was never the root cause.

THE ROOT CAUSE IS A MISSING DENOMINATOR. `_MIN_RECURRENCE = 3` escalates any
capability with three precisely-attributed failures inside a 7-day lookback. It
is an absolute count with nothing under it, so a capability the platform uses
constantly crosses it permanently, gets re-diagnosed every 24h for ever, and the
RCA finds nothing because there is nothing to find. Seven days, 2,395 turns:

    capability          failed  ran in   rate      z
    browser_navigate        66     163   40.5%   14.8
    web_fetch               60     145   41.4%   14.4
    shell                   36     158   22.8%    6.6
    read_file               14     148    9.5%    0.5   <- pooled 8.35%
    todo                     3      25   12.0%    0.7

`read_file` at 9.5% against a platform-wide 8.35% is normal, and `todo` was
three failures in total. Both opened incidents. 86 of 100 RCA verdicts came back
unverified — a detector running at roughly 10% precision at 140,000 tokens a
shot. This is the "check what a denominator is MADE OF" rule, one layer down:
the detector was reading only the numerator.

WHAT THE SAME CAUSE ALSO REACHES. Every capability class, not just the five that
happened to fire — any tool the platform calls regularly is permanently over an
absolute floor of three. And the gate is scale-aware precisely because a bare
rate is not: 3-in-25 and 60-in-145 are both "above average" and only the second
is distinguishable from chance.

WHAT IS DELIBERATELY UNCHANGED. Health incidents (SOURCE 1) do not pass through
this gate — a subsystem still down after a recycle is an incident whatever the
tool failure rates say. And the gate FAILS TOWARD DIAGNOSING: no rates means
every cluster escalates exactly as before, because a gate that silenced the
self-heal loop on a failed query would be the failure mode this arc exists to
prevent.
"""

from __future__ import annotations

import math

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import CapabilityFailureRates, TaskOutcomeStore
from stackowl.scheduler.handlers.incident_escalation import _ANOMALY_Z
from tests._schema_template import seed_schema

# The live seven-day measurement, used as the fixture so the numbers under test
# are the platform's own rather than invented ones.
_LIVE_TURNS = {
    "note_applied_lesson": 606, "skill_view": 366, "memory": 273,
    "browser_navigate": 163, "shell": 158, "read_file": 148, "web_fetch": 145,
    "web_search": 115, "write_file": 87, "tool_search": 80, "todo": 25,
}
_LIVE_FAILURES = {
    "browser_navigate": 66, "web_fetch": 60, "shell": 36, "read_file": 14,
    "memory": 16, "web_search": 4, "todo": 3, "write_file": 2,
}


def _live() -> CapabilityFailureRates:
    return CapabilityFailureRates(failures=_LIVE_FAILURES, turns=_LIVE_TURNS)


# --------------------------------------------------------------------------- #
# The arithmetic, against the live numbers                                     #
# --------------------------------------------------------------------------- #


def test_the_pooled_baseline_is_the_platforms_own_rate() -> None:
    rates = _live()
    # 9.3% here against 8.35% live: the fixture carries the eleven busiest
    # capabilities rather than all thirty, so its denominator is smaller. The
    # property under test is that the baseline is COMPUTED from the rows.
    assert 0.08 < rates.pooled_rate() < 0.11, (
        "the baseline must be derived from the data, not configured — it is what "
        "makes a fleet-wide bad day raise the bar instead of opening an incident "
        "against every capability at once"
    )


@pytest.mark.parametrize("capability", ["browser_navigate", "web_fetch", "shell"])
def test_a_genuinely_broken_capability_still_escalates(capability: str) -> None:
    """The expensive direction. A wrong suppression here silently disables the
    self-heal loop for a capability that IS failing."""
    assert _live().z_score(capability) >= _ANOMALY_Z


@pytest.mark.parametrize("capability", ["read_file", "todo", "memory", "web_search"])
def test_ordinary_failure_is_no_longer_an_incident(capability: str) -> None:
    """The 19M-token defect: each of these opened an incident on a raw count."""
    assert _live().z_score(capability) < _ANOMALY_Z


def test_the_gate_is_scale_aware_not_just_a_rate_bar() -> None:
    """3-in-25 (12%) and 60-in-145 (41%) are both above the 8.35% baseline. Only
    one is distinguishable from chance, and a plain rate comparison cannot tell
    them apart — which is why this is a z-score and not a percentage."""
    rates = _live()
    assert rates.failures["todo"] / rates.turns["todo"] > rates.pooled_rate()
    assert rates.z_score("todo") < _ANOMALY_Z


def test_a_capability_that_never_ran_invents_no_anomaly() -> None:
    assert _live().z_score("a_tool_that_has_never_run") == 0.0


def test_an_empty_window_is_not_an_anomaly() -> None:
    """"An empty table is a QUESTION, not an answer" — zero data must never read
    as "everything is broken" and open an incident against every capability."""
    empty = CapabilityFailureRates(failures={}, turns={})
    assert empty.pooled_rate() == 0.0
    assert empty.z_score("shell") == 0.0


def test_a_capability_failing_every_time_is_far_over_the_bar() -> None:
    rates = CapabilityFailureRates(
        failures={"broken": 40, "fine": 5}, turns={"broken": 40, "fine": 200},
    )
    assert rates.z_score("broken") > _ANOMALY_Z
    assert rates.z_score("fine") < _ANOMALY_Z


def test_the_z_score_matches_the_stated_formula() -> None:
    """The bar is only defensible if the arithmetic behind it is the stated one."""
    rates = CapabilityFailureRates(
        failures={"a": 30}, turns={"a": 100, "b": 100},
    )
    p0 = rates.pooled_rate()
    expected = (0.30 - p0) / math.sqrt(p0 * (1.0 - p0) / 100)
    assert rates.z_score("a") == pytest.approx(expected)


def test_the_bar_is_three_sigma_and_the_reasoning_lives_beside_it() -> None:
    """The number alone is not the fix — a later reader lowering it "to catch
    more" would restore the 19M-token treadmill, so the measurement that
    justifies it must be written where they will see it."""
    import inspect

    from stackowl.scheduler.handlers import incident_escalation

    assert _ANOMALY_Z == 3.0
    marker = inspect.getsource(incident_escalation).split("_ANOMALY_Z = 3.0")[0][-2200:]
    assert "8.35" in marker and "19,167,115" in marker, (
        "the measurement that justifies the bar is not stated next to it"
    )


# --------------------------------------------------------------------------- #
# The query, against a real database                                           #
# --------------------------------------------------------------------------- #

pytestmark_async = pytest.mark.asyncio


@pytest.fixture
async def store(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    path = tmp_path / "outcomes.db"
    pool = DbPool(db_path=path)
    await pool.open()
    seed_schema(path)
    yield TaskOutcomeStore(pool), pool
    await pool.close()


_next_trace = iter(f"trace-{i}" for i in range(10_000))


async def _row(pool: DbPool, *, seq: str, failed: str | None, at: float) -> None:
    await pool.execute(
        "INSERT INTO task_outcomes (trace_id, session_key, owl_name, channel,"
        " success, latency_ms, tool_sequence, failed_capability, captured_at)"
        " VALUES (?,'s','o','cli', ?, 0, ?, ?, ?)",
        (next(_next_trace), 0 if failed else 1, seq, failed, at),
    )


@pytest.mark.asyncio
async def test_the_denominator_counts_turns_not_calls(store) -> None:  # noqa: ANN001
    """A tool called five times in one turn is ONE turn — the numerator is a
    per-turn attribution, so the denominator must be per-turn too or the rate is
    silently deflated and nothing ever crosses the bar."""
    index, pool = store
    await _row(pool, seq='["shell","shell","shell","shell","shell"]',
               failed=None, at=100.0)
    rates = await index.capability_failure_rates(since_epoch=0.0)
    assert rates.turns["shell"] == 1


@pytest.mark.asyncio
async def test_numerator_and_denominator_share_one_window(store) -> None:  # noqa: ANN001
    """Different windows would make the rate meaningless in a way no test of
    either half alone could catch."""
    index, pool = store
    await _row(pool, seq='["shell"]', failed="shell", at=10.0)   # outside
    await _row(pool, seq='["shell"]', failed="shell", at=500.0)  # inside
    await _row(pool, seq='["shell"]', failed=None, at=500.0)     # inside
    rates = await index.capability_failure_rates(since_epoch=100.0)
    assert rates.turns["shell"] == 2
    assert rates.failures["shell"] == 1


@pytest.mark.asyncio
async def test_an_empty_tool_sequence_is_not_a_capability(store) -> None:  # noqa: ANN001
    index, pool = store
    await _row(pool, seq="[]", failed=None, at=500.0)
    await _row(pool, seq="", failed=None, at=500.0)
    rates = await index.capability_failure_rates(since_epoch=0.0)
    assert rates.turns == {}


@pytest.mark.asyncio
async def test_a_broken_read_returns_no_opinion(store, monkeypatch) -> None:  # noqa: ANN001
    """Which the caller treats as "escalate as before" — never as "nothing is
    wrong". A gate that silenced the self-heal loop on a failed query would be
    the failure mode this whole arc exists to prevent."""
    index, pool = store

    async def _boom(*a: object, **k: object) -> list[dict]:
        raise RuntimeError("no such table")

    monkeypatch.setattr(pool, "fetch_all", _boom)
    rates = await index.capability_failure_rates(since_epoch=0.0)
    assert rates.turns == {} and rates.failures == {}
    assert rates.z_score("shell") == 0.0
