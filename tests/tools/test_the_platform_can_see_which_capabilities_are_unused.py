"""One definition of "never invoked", asked by everyone who needs it.

MEASURED 2026-09-01 over the live 20k-row task_outcomes:

  note_applied_lesson   786 invocations, most recent minutes ago
  reflect_now            11 invocations since 2026-06-19
  synthesize_skills       0, all-time
  evolve_now              0, all-time — while holding a GUARANTEED presentation
                          slot on every single turn

THE CONTRAST IS THE FINDING, and it is not "the model ignores learning". A tool
that records something as a BYPRODUCT of answering is used constantly; tools that
ask the model to stop and do meta-work instead of finishing the user's task are
never chosen. That is an incentive shape, not a discoverability bug, and
presenting them harder would not change it.

THIS RULE LIVED IN ``learned_tool_loader`` FIRST and answered the question for
two learned tools — the example, not the architecture. The same blindness covers
all 79 registered tools and hides more there. The loader now ASKS this function
rather than carrying its own copy, because two definitions of "never invoked"
would drift exactly where it matters.

NOTHING IS REMOVED ON THE STRENGTH OF IT. Zero invocations is not proof of
uselessness: a tool the presentation cap dropped was never OFFERED, and
``skills_list`` already cost this project that exact mistake. What was missing is
that the accumulation was invisible.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.tools._infra.usage_report import report_never_invoked

pytestmark = pytest.mark.asyncio


class _Db:
    def __init__(self, used: tuple[str, ...]) -> None:
        self._used = used

    async def fetch_all(self, sql: str, *a: object) -> list[dict]:
        assert "tool_sequence" in sql, "usage must come from the recorded sequences"
        return [{"tool": t} for t in self._used]


class _AngryDb:
    async def fetch_all(self, *a: object, **k: object) -> list[dict]:
        raise RuntimeError("no such table")


async def test_it_names_the_live_never_invoked_tools(caplog) -> None:  # noqa: ANN001
    """The measured case: evolve_now and synthesize_skills, zero all-time."""
    with caplog.at_level(logging.INFO):
        never = await report_never_invoked(
            ["evolve_now", "synthesize_skills", "reflect_now", "note_applied_lesson"],
            _Db(used=("reflect_now", "note_applied_lesson", "shell")),
            scope="all",
        )
    assert never == ["evolve_now", "synthesize_skills"]
    rec = [r for r in caplog.records if "capability usage" in r.getMessage()]
    assert rec and rec[0]._fields["scope"] == "all"  # noqa: SLF001
    assert rec[0]._fields["n_never_invoked"] == 2  # noqa: SLF001


async def test_a_USED_tool_is_never_named(caplog) -> None:  # noqa: ANN001
    """The expensive direction: naming a working tool as unused invites someone
    to remove a capability that is in daily use — note_applied_lesson has 786."""
    never = await report_never_invoked(
        ["note_applied_lesson"], _Db(used=("note_applied_lesson",)), scope="all",
    )
    assert never == []


async def test_no_pool_reports_UNKNOWN_not_zero(caplog) -> None:  # noqa: ANN001
    """An unknown must never read as "none"."""
    with caplog.at_level(logging.INFO):
        assert await report_never_invoked(["x"], None, scope="all") == []
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "UNKNOWN" in msg
    assert "capability usage" not in msg, (
        "a pool-less report emitted a usage verdict it could not have"
    )


async def test_a_broken_read_reports_nothing_rather_than_a_false_zero(caplog) -> None:  # noqa: ANN001
    with caplog.at_level(logging.WARNING):
        assert await report_never_invoked(["x"], _AngryDb(), scope="all") == []
    assert any("false zero" in r.getMessage() for r in caplog.records)


async def test_an_empty_registry_says_nothing_at_all() -> None:
    assert await report_never_invoked([], _Db(used=()), scope="all") == []
    assert await report_never_invoked(["", "  "], _Db(used=()), scope="all") == []


async def test_reporting_never_costs_the_boot() -> None:
    """A usage REPORT must not be the thing that wedges startup."""
    assert await report_never_invoked(["x"], object(), scope="all") == []


async def test_the_learned_loader_ASKS_this_rule_rather_than_copying_it() -> None:
    """Structural. Two definitions of "never invoked" would drift exactly where
    it matters — this is the "one source; have the other ask it" shape."""
    import inspect

    from stackowl.tools.meta.learned_tool_loader import LearnedToolLoader

    src = inspect.getsource(LearnedToolLoader._report_unused)  # noqa: SLF001
    assert "report_never_invoked" in src
    assert "json_each" not in src, "the loader still carries its own copy of the query"


async def test_it_is_WIRED_for_the_whole_registry() -> None:
    """A feature ships ON. Reporting only learned tools would leave the bigger
    blind spot exactly as it was."""
    import inspect

    from stackowl.startup import orchestrator

    src = inspect.getsource(orchestrator)
    assert 'scope="all"' in src, "the registry-wide report is never called"
    assert "tool_registry.all()" in src
