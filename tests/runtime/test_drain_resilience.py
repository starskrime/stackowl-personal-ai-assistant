"""F-37 — quiesce notifies the user on a straggler abandon + only claims durable
resume when a checkpoint actually exists."""

from __future__ import annotations

import pytest

from stackowl.runtime.drain import quiesce


class _NeverDrains:
    """Always reports an active turn so the grace ceiling is always hit."""

    def has_active_turns(self) -> bool:
        return True

    def active_turn_count(self) -> int:
        return 1


@pytest.mark.asyncio
async def test_straggler_emits_user_notice() -> None:
    """A straggler past grace must produce a user-facing 'interrupted, retrying'
    notice via the sink (not silently abandoned)."""
    notices: list[str] = []

    async def _sink(msg: str) -> None:
        notices.append(msg)

    drained = await quiesce(
        _NeverDrains(),
        grace_seconds=0.02,
        poll_interval_s=0.01,
        notify=_sink,
    )

    assert drained is False
    assert len(notices) == 1
    assert "interrupted" in notices[0].lower()


@pytest.mark.asyncio
async def test_resumable_only_when_checkpoint_exists() -> None:
    """``has_checkpoint`` returning False means we must NOT claim durable resume."""
    notices: list[str] = []

    async def _sink(msg: str) -> None:
        notices.append(msg)

    drained = await quiesce(
        _NeverDrains(),
        grace_seconds=0.02,
        poll_interval_s=0.01,
        notify=_sink,
        has_checkpoint=lambda: False,
    )

    assert drained is False
    # With no checkpoint the user notice must not promise automatic resume.
    assert "resume" not in notices[0].lower() or "not" in notices[0].lower()


@pytest.mark.asyncio
async def test_notify_failure_never_raises() -> None:
    """A broken notify sink must not break the restart drain."""

    async def _boom(_msg: str) -> None:
        raise RuntimeError("sink down")

    drained = await quiesce(
        _NeverDrains(),
        grace_seconds=0.02,
        poll_interval_s=0.01,
        notify=_boom,
    )
    assert drained is False  # still returns the abandon verdict


@pytest.mark.asyncio
async def test_clean_drain_no_notice() -> None:
    """No straggler → no user notice (only the ceiling path notifies)."""
    notices: list[str] = []

    class _Idle:
        def has_active_turns(self) -> bool:
            return False

        def active_turn_count(self) -> int:
            return 0

    async def _sink(msg: str) -> None:
        notices.append(msg)

    drained = await quiesce(_Idle(), grace_seconds=1.0, notify=_sink)
    assert drained is True
    assert notices == []
