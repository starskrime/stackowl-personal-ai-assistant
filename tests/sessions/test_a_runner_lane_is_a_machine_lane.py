"""The lane vocabulary moved; the predicate that reads it did not.

``is_machine_lane`` was shipped 2026-08-25, and its own caller records why:
"4,480 of 5,212 staged rows turned out to be the platform's own prompts". It was
a prefix check on ``goal-`` and ``incident-`` — the machine lane names of that
day. ``build_session_key`` has since minted a second, structurally different
machine lane and the predicate was never told::

    if source.runner and source.runner_id:
        return "owl:{owl}:{runner}:{runner_id}"      # a RUNNER lane

    parts = ["owl", owl, channel, chat_type.value]   # a CHAT lane

The builder's own comment already draws the line — a runner lane is keyed "by
what it is and which one", because isolation asks "whose messages are these, a
question a cron job or an objective does not have". So the platform knows
perfectly well which lanes have nobody behind them. The predicate just was not
asking.

MEASURED 2026-08-31 on the live database. Of 368 staged_facts, **178** sit on a
lane with no person on it — ``owl:secretary:recovery:task-...`` (99),
``owl:secretary:objective:obj-...`` (74), plus 5 of the old prefixed form. Every
one was filed as a durable fact ABOUT THE USER. The live ``sessions`` table shows
the same split cleanly: every ``owl:*:telegram:dm:72055773`` is a person, every
``owl:*:recovery:*`` and ``owl:*:objective:*`` is not.

THE DISCRIMINATOR IS STRUCTURAL AND WE OWN IT. A chat lane's fourth segment is a
``ChatType`` — a closed enum with four values. A runner lane's fourth segment is
a runner id. So "does segment 4 name a chat type" separates them exactly, with no
vocabulary of runner names to keep in sync — ``objective``, ``cron``,
``subagent`` and ``recovery`` are free text on ``SessionSource`` and a list of
them would be the next thing to drift.

THE DANGEROUS DIRECTION IS ONE-WAY, and the docstring already says so: "a wrong
answer here silently drops real user facts". Every test below that asserts
``False`` is protecting that direction.
"""

from __future__ import annotations

import pytest

from stackowl.sessions.models import (
    ChatType,
    SessionSource,
    build_session_key,
    is_machine_lane,
)

# ------------------------------------------------- the lanes measured live


@pytest.mark.parametrize("key", [
    "owl:secretary:recovery:task-5119994",
    "owl:secretary:objective:obj-1ad8a5fa",
    "owl:rca_gatherer:recovery:retry-8b7c40",
    "owl:Brain:recovery:task-e0300d3",
    "owl:jobmarket:recovery:task-86b2571",
])
def test_a_runner_lane_has_nobody_behind_it(key: str) -> None:
    assert is_machine_lane(key) is True, (
        f"{key} staged facts ABOUT THE USER — 178 of 368 rows on the live "
        f"database came from lanes exactly like this one"
    )


@pytest.mark.parametrize("key", [
    "owl:secretary:telegram:dm:72055773",
    "owl:scout:telegram:dm:72055773",
    "owl:jobmarket:telegram:dm:72055773",
    "owl:secretary:slack:channel:C0123:thread-9",
    "owl:secretary:telegram:group:-100123:alice",
])
def test_a_CHAT_LANE_IS_NEVER_MACHINE(key: str) -> None:
    """The expensive direction. A false positive here silently discards a real
    memory and nothing reports it."""
    assert is_machine_lane(key) is False


def test_the_old_prefixed_lanes_still_match() -> None:
    """5 rows on the live database still carry the 2026-08-25 forms. Recognising
    the new shape must not stop recognising the old one."""
    assert is_machine_lane("goal-owl_lifecycle-jobmarket") is True
    assert is_machine_lane("incident-b5545c2ec371") is True


# ------------------------------------------ derived from the builder, not restated


@pytest.mark.parametrize("chat_type", list(ChatType))
def test_EVERY_chat_type_the_enum_defines_reads_as_human(chat_type: ChatType) -> None:
    """Generated FROM the enum, so a fifth ChatType added later cannot silently
    start classifying a real conversation as a machine lane."""
    key = build_session_key(SessionSource(
        owl_name="secretary", channel="telegram", chat_type=chat_type,
        chat_id="72055773",
    ))
    assert is_machine_lane(key) is False, key


@pytest.mark.parametrize("runner", ["objective", "cron", "subagent", "recovery", "whatever"])
def test_EVERY_runner_kind_reads_as_machine(runner: str) -> None:
    """``runner`` is free text on SessionSource — deliberately, so the platform can
    mint a new kind of background work without a migration. The predicate must not
    need to know the list, or the next new runner leaks facts the way recovery did."""
    key = build_session_key(SessionSource(
        owl_name="secretary", channel="internal", runner=runner, runner_id="r-1",
    ))
    assert is_machine_lane(key) is True, key


def test_the_two_builder_branches_are_told_apart_for_the_SAME_owl() -> None:
    """One owl, both lanes, opposite answers — which is the whole point."""
    human = build_session_key(SessionSource(
        owl_name="secretary", channel="telegram", chat_id="72055773",
    ))
    machine = build_session_key(SessionSource(
        owl_name="secretary", channel="internal", runner="recovery", runner_id="task-1",
    ))
    assert (is_machine_lane(human), is_machine_lane(machine)) == (False, True)


# ------------------------------------------------------- degenerate input


@pytest.mark.parametrize("key", ["", None, "owl", "owl:secretary", "owl:secretary:telegram"])
def test_anything_it_cannot_read_is_treated_as_HUMAN(key: str | None) -> None:
    """Fail toward keeping the memory. An unparseable key is a question, and the
    answer that loses data is the wrong one to guess."""
    assert is_machine_lane(key) is False


def test_a_key_that_is_not_a_lane_at_all_is_human() -> None:
    """`session_key` is not always a lane the builder minted — the CLI and tests
    pass their own strings."""
    assert is_machine_lane("cli") is False
    assert is_machine_lane("some:random:string:here") is False


# ------------------------------------------- the other lanes the platform mints


@pytest.mark.parametrize("key,minted_by", [
    ("job:morning_brief-9fb1c485", "scheduler/scheduler.py:133"),
    ("recover-925aa68fix12", "pipeline/durable/recovery.py:509"),
    ("shadow-validate-abc123", "owls/shadow_validator.py:271"),
])
def test_every_lane_the_platform_mints_for_itself_is_machine(key: str, minted_by: str) -> None:
    """MEASURED, not guessed. Scanning every table that carries a session_key
    found 4,150 distinct lanes; beyond goal-/incident- there were 523 shadow-,
    96 job: and 23 recover-, each minted in src/ with no user input near it.
    ``recover-`` matters most — it is the durable retry driver, the same family
    as the RetryActuator whose own prompts were being staged as user facts."""
    assert is_machine_lane(key) is True, f"{key} is minted by {minted_by}"


def test_a_user_could_not_produce_one_of_these_keys() -> None:
    """Why widening the prefix list is safe: every human lane begins `owl:`."""
    assert is_machine_lane("owl:secretary:telegram:dm:jobseeker") is False
