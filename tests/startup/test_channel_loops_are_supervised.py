"""A channel's receive loop must survive its own exceptions.

Bakir, 2026-08-28: "Platform not stable. Becomes unhealthy a lot."

MEASURED, and it is not general rot — it is one shape repeated. On 2026-08-28 at
01:13:55 the Telegram liveness heartbeat died with
``OperationalError('database is locked')`` and was never restarted, so
``channel_liveness.telegram`` froze and the health sweep reported
``degraded=['telegram_receive']`` twelve times over 24 hours WHILE TELEGRAM WAS
WORKING (8 messages arrived in that window). A false alarm that can never clear.

THE AUDIT BEHIND THIS TEST. 49 ``create_task`` sites in src/; 23 are neither
supervised nor self-healing; 12 of those are LONG-LIVED daemons — including
``_telegram_loop``, ``_slack_loop``, ``_discord_loop``, ``_whatsapp_loop``, the
watchdog's own ``_ping_loop`` and the browser session sweep. Every channel's
receive loop is a bare task: one escape from its inner per-message guard and the
platform is silently unreachable on that channel until someone restarts it.

AND THE FIX ALREADY EXISTED. ``stackowl.supervisor.supervisor`` does exponential
backoff restart, a consecutive-failure ceiling with escalation, a stuck-task
watchdog and a tight-loop guard — and it is ALREADY PROVEN in production by the
job scheduler and the webhook receiver. It simply was not applied to the loops
whose death makes the platform unreachable. This is the "capability built, not
wired" shape, not a missing capability.

These tests pin the property that matters: a loop that raises comes BACK.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.supervisor.supervisor import Supervisor, make_supervised_task


@pytest.mark.asyncio
async def test_a_loop_that_raises_is_restarted() -> None:
    """THE regression. A bare create_task dies once and stays dead."""
    runs = 0
    revived = asyncio.Event()

    async def _flaky_receive_loop() -> None:
        nonlocal runs
        runs += 1
        if runs == 1:
            raise RuntimeError("database is locked")
        revived.set()
        await asyncio.sleep(3600)

    sup = Supervisor()
    sup.register(make_supervised_task("telegram_receive", _flaky_receive_loop))
    await sup.start()
    try:
        await asyncio.wait_for(revived.wait(), timeout=10)
    finally:
        await sup.stop()

    assert runs >= 2, "the loop died on its first exception and never came back"


@pytest.mark.asyncio
async def test_a_permanently_broken_loop_ESCALATES_rather_than_spinning(monkeypatch) -> None:
    """Restarting for ever is its own failure mode.

    The heartbeat outage was invisible for a day. A loop that can never start must
    reach a human, which is what the supervisor's consecutive-failure ceiling is
    for — otherwise 'supervised' just means 'fails quietly at higher frequency'.
    """
    escalations: list[str] = []

    async def _always_broken() -> None:
        raise RuntimeError("misconfigured for ever")

    # Real backoff is 1+2+4+8+16 = 31s to reach the 5-failure ceiling. Shrunk here
    # so the test measures the ESCALATION, not the clock — the first version waited
    # 10s and failed on timing rather than on behaviour.
    import stackowl.supervisor.supervisor as sup_mod

    monkeypatch.setattr(sup_mod, "_BACKOFF_INITIAL", 0.01)
    monkeypatch.setattr(sup_mod, "_BACKOFF_MAX", 0.05)

    sup = Supervisor(on_escalation=lambda ev: escalations.append(ev.task_id))
    sup.register(make_supervised_task("slack_receive", _always_broken))
    await sup.start()
    try:
        for _ in range(200):
            if escalations:
                break
            await asyncio.sleep(0.05)
    finally:
        await sup.stop()

    assert escalations == ["slack_receive"], (
        "a permanently broken channel loop never reached anyone"
    )


def test_no_channel_receive_loop_is_started_bare() -> None:
    """The wiring property, and the one that keeps this true for loops nobody has
    written yet.

    A supervisor nothing registers with is the same defect as no supervisor. This
    fails if a channel loop goes back to a bare asyncio.create_task.
    """
    import pathlib
    import re

    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src" / "stackowl" / "startup" / "orchestrator.py"
    ).read_text(encoding="utf-8")

    bare = re.findall(
        r"asyncio\.create_task\(\s*_(telegram|slack|discord|whatsapp)_loop\(\)\s*\)", src
    )
    assert not bare, (
        f"these channel receive loops are started unsupervised: {sorted(set(bare))} — "
        "one exception and the platform is silently unreachable on that channel"
    )
