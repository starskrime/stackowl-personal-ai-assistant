"""Cross-platform kill + liveness probe — dead-pid no-op, self-pid alive (E9-S0)."""

from __future__ import annotations

import os

import pytest

from stackowl.process.kill_platform import is_pid_alive, terminate_tree


def test_self_pid_is_alive() -> None:
    assert is_pid_alive(os.getpid()) is True


def test_absurd_pid_is_dead() -> None:
    assert is_pid_alive(999_999_999) is False


def test_none_and_zero_are_dead() -> None:
    assert is_pid_alive(None) is False
    assert is_pid_alive(0) is False


@pytest.mark.asyncio
async def test_terminate_dead_pid_is_noop_success() -> None:
    # Kill of an already-dead pid must be a no-op (False), never raise.
    assert await terminate_tree(999_999_999) is False


@pytest.mark.asyncio
async def test_terminate_none_is_noop() -> None:
    assert await terminate_tree(None) is False
