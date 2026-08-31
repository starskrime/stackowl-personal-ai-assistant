"""One RCA per tick — or the budget that keeps it under the ceiling is a fiction.

MEASURED 2026-08-31 over 6,684 runs of ``incident_escalation``: p50 0.3s, p90
13s, max 1198.5s, with 8 dispatch timeouts at the scheduler's 1200s ceiling. A
handler whose median is a third of a second does not reach twenty minutes by
being slow.

THE BUDGET IT DEFEATS IS EXPLICIT AND TESTED. ``staged_rca`` reasons:

    "The ceiling that makes this safe is the scheduler's own _HANDLER_TIMEOUT_SEC
    (1200s) — worst case here is three stages plus one retry (4 x 240 = 960s),
    leaving 240s of margin, and a test pins that relationship so the inner budget
    can never be silently pre-empted by the outer one."

That reasoning is correct FOR ONE RCA. ``execute`` runs ``for inc in
new_incidents: await self._resolve_incident(...)`` — sequentially, unbounded. Two
new incidents in one tick is up to 1920s against a 1200s ceiling: a guaranteed
timeout, and the pinned 240s margin never had a chance.

WORSE, THE TIMEOUT DOES NOT STOP IT. The scheduler's own comment records the
measurement: ``asyncio.wait_for`` cancels the awaited coroutine, not the tasks it
spawned, so a timed-out RCA keeps running — "incident_escalation logged 'RCA
complete' NINE MINUTES after its own timeout" — while the row sits 'running'
until reaped at 2400s. So the second RCA is not merely late; it runs untracked
and blocks detection for up to forty minutes.

NOTHING IS LOST BY DEFERRING. The handler already assumes incidents persist
across ticks — it dedupes to "one incident, one RCA" and its own comment says the
NEXT tick retries a persistent incident. The tick is every 10 minutes.
"""

from __future__ import annotations

from stackowl.scheduler.handlers.incident_escalation import MAX_RCA_PER_TICK

# Every test here is synchronous — these are arithmetic invariants, not behaviour.


def test_at_most_one_rca_runs_per_tick() -> None:
    """The constant that keeps the staged-RCA budget honest.

    staged_rca's worst case is 4 x 240 = 960s. The scheduler's ceiling is 1200s.
    Two RCAs in one tick is 1920s, so the pinned margin only exists while this
    is 1.
    """
    from stackowl.parliament.staged_rca import DEFAULT_PER_STAGE_TIMEOUT_S
    from stackowl.scheduler.scheduler import _HANDLER_TIMEOUT_SEC

    stages_plus_retry = 4
    worst_case = MAX_RCA_PER_TICK * stages_plus_retry * DEFAULT_PER_STAGE_TIMEOUT_S
    assert worst_case < _HANDLER_TIMEOUT_SEC, (
        f"{MAX_RCA_PER_TICK} RCA(s) x {stages_plus_retry} stages x "
        f"{DEFAULT_PER_STAGE_TIMEOUT_S}s = {worst_case}s, which is not under the "
        f"scheduler's {_HANDLER_TIMEOUT_SEC}s ceiling — the inner budget is being "
        f"pre-empted by the outer one, which is exactly what staged_rca's own test "
        f"exists to prevent"
    )


def test_the_margin_staged_rca_reasons_about_still_exists() -> None:
    """staged_rca claims 240s of margin. Assert the claim, do not trust the prose."""
    from stackowl.parliament.staged_rca import DEFAULT_PER_STAGE_TIMEOUT_S
    from stackowl.scheduler.scheduler import _HANDLER_TIMEOUT_SEC

    worst_case = MAX_RCA_PER_TICK * 4 * DEFAULT_PER_STAGE_TIMEOUT_S
    assert _HANDLER_TIMEOUT_SEC - worst_case >= 240.0


def test_the_cap_is_one_and_the_reasoning_lives_beside_it() -> None:
    """The number alone is not the fix — a later reader raising it "to catch up
    faster" would silently restore the guaranteed timeout, so the ceiling
    arithmetic must be written where they will see it."""
    import inspect

    from stackowl.scheduler.handlers import incident_escalation

    assert MAX_RCA_PER_TICK == 1
    source = inspect.getsource(incident_escalation)
    marker = source.split("MAX_RCA_PER_TICK = 1")[0][-1600:]
    assert "1200" in marker and "960" in marker, (
        "the ceiling arithmetic is not stated next to the constant it justifies"
    )
