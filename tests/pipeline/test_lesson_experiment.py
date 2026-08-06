"""ADR-19 #4 — make lesson injection falsifiable.

Lessons ARE injected on every turn and 2,680 are stored, so the feedback leg is
closed. What was missing is obligation (3), VERIFY: nothing measured whether
injecting them helps. The only signal was `note_applied_lesson`, a tool the model
must volunteer to call — called ONCE in fifteen days across 2,193 turns.

The load-bearing test in this file is the SESSION-STABILITY one. The block is
part of the system prompt, so a per-turn decision would change the prompt
mid-conversation — reintroducing the exact defect D01.1 fixed, in the name of
measuring a different one.
"""

from __future__ import annotations

import pytest

from stackowl.infra.lesson_experiment import (
    ARM_HELD_OUT,
    ARM_INJECTED,
    arm_for_session,
    current_arm,
    resolve_and_record,
    set_arm,
)


# --------------------------------------------------------------------------- #
# Law 1: the arm must never change within a conversation.
# --------------------------------------------------------------------------- #


def test_a_session_gets_the_SAME_arm_every_turn():
    """THE POINT. A session that flipped arms would send a different system
    prompt on its second turn and destroy the per-conversation cache."""
    arms = {arm_for_session("sess-abc", hold_out_percent=50) for _ in range(200)}
    assert len(arms) == 1, "a session must resolve to exactly one arm, always"


def test_the_arm_survives_a_restart_and_a_second_process():
    """Hashed, not random: the gateway and core are SEPARATE PROCESSES and both
    assemble prompts. Python's hash() is salted per process, so using it would
    give the same session different arms in each — a prompt that changes
    depending on which process served the turn."""
    import subprocess
    import sys

    code = (
        "from stackowl.infra.lesson_experiment import arm_for_session;"
        "print(arm_for_session('stable-session', hold_out_percent=50))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == arm_for_session("stable-session", hold_out_percent=50)


def test_different_sessions_land_in_different_arms():
    """A split that put everything in one arm would answer nothing."""
    arms = {arm_for_session(f"sess-{i}", hold_out_percent=50) for i in range(200)}
    assert arms == {ARM_INJECTED, ARM_HELD_OUT}


def test_the_split_is_roughly_the_requested_rate():
    n = 4000
    held = sum(
        1 for i in range(n)
        if arm_for_session(f"s{i}", hold_out_percent=20) == ARM_HELD_OUT
    )
    assert 0.15 < held / n < 0.25, f"expected ~20%, got {held / n:.0%}"


# --------------------------------------------------------------------------- #
# Turning it off, and refusing to hold out what cannot be compared.
# --------------------------------------------------------------------------- #


def test_zero_percent_disables_the_experiment_entirely():
    """The reversal path. At 0 every turn is the control and behaviour is
    exactly as before ADR-19 #4."""
    for i in range(500):
        assert arm_for_session(f"s{i}", hold_out_percent=0) == ARM_INJECTED


def test_a_sessionless_turn_is_never_held_out():
    """Background and utility calls have no conversation to hold out. Degrading
    them would contaminate the comparison with turns that were never
    comparable."""
    assert arm_for_session(None, hold_out_percent=100) == ARM_INJECTED
    assert arm_for_session("", hold_out_percent=100) == ARM_INJECTED


def test_one_hundred_percent_holds_everything_out():
    assert arm_for_session("any", hold_out_percent=100) == ARM_HELD_OUT


# --------------------------------------------------------------------------- #
# The carrier.
# --------------------------------------------------------------------------- #


def test_the_default_arm_is_the_CONTROL():
    """Fail-safe: a turn whose arm was never resolved must look like an ordinary
    injected turn, never like a held-out one — otherwise an unrelated bug would
    read as evidence that lessons don't help."""
    assert current_arm() == ARM_INJECTED


def test_resolve_records_the_arm_for_the_outcome_writer():
    resolve_and_record("sess-held" if False else None)
    assert current_arm() == ARM_INJECTED
    set_arm(ARM_HELD_OUT)
    assert current_arm() == ARM_HELD_OUT
    set_arm(ARM_INJECTED)


def test_resolve_returns_what_it_records():
    for key in ("a", "b", "c", "d", "e"):
        assert resolve_and_record(key) == current_arm()


@pytest.mark.asyncio
async def test_the_arm_does_not_leak_between_concurrent_turns():
    """ContextVar, per async context. Two turns in flight must not see each
    other's arm, or the recorded label is worse than none."""
    import asyncio

    seen: dict[str, str] = {}

    async def _turn(key: str) -> None:
        resolve_and_record(key)
        await asyncio.sleep(0)  # force interleaving
        seen[key] = current_arm()

    keys = [f"sess-{i}" for i in range(20)]
    await asyncio.gather(*(asyncio.create_task(_turn(k)) for k in keys))

    for k in keys:
        assert seen[k] == arm_for_session(k), f"{k} saw another turn's arm"
