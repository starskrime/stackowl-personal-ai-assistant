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

THAT CAVEAT IS NOW SETTLED, and it took a join this report cannot do itself.
MEASURED 2026-09-03 over ``[pipeline] execute: context budget``, which records
``tools_count`` on every turn: 1,539 of 2,020 turns (76%) presented ALL 79
registered tools, and ``[presentation] select: eligible tools NOT presented``
appears ZERO times in seven days of logs. So nothing was being dropped — the
never-invoked fifteen were OFFERED on three turns in four and refused. The
caveat above is real but does not apply here; the reading is "declined", not
"never had the chance". (For scale: tool schemas are 19,423 of a 22,786-token
median round — 85% — at ~246 tokens each, so those fifteen cost ~3,700 tokens
every round.)

AND THE REPORT HAD THE SAME HOLE IT WAS BUILT TO CLOSE. Its own rule — "an
UNKNOWN must not read as NONE" — was applied to the missing-pool case only. With
a pool that returns NO invocation history, ``used`` is empty, so EVERY registered
name comes back never-invoked and the line looks exactly like a real finding. A
numerator with no denominator, in the module written to fix numerators with no
denominators. The evidence base is now on the line, and an empty history reports
UNKNOWN rather than accusing all 79.
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


# --------------------------------------------------------------------------- #
# The report must be readable as evidence, not just as a number                #
# --------------------------------------------------------------------------- #


async def test_an_empty_history_is_UNKNOWN_not_everything_is_unused(caplog) -> None:  # noqa: ANN001
    """THE DEFECT. A pool that returns no invocations makes every registered name
    "never invoked" — 79 of 79 — and the line is shaped identically to a genuine
    finding. This module states the rule for a missing POOL and never applied it
    to missing DATA. Same rule, one case short."""
    with caplog.at_level(logging.INFO, logger="stackowl.tool"):
        never = await report_never_invoked(("alpha", "beta", "gamma"), _Db(()), scope="all")

    assert never == [], (
        "with no invocation history at all, every tool was reported unused — a "
        "zero numerator over a zero denominator is not a finding"
    )
    assert "UNKNOWN" in caplog.text, caplog.text


async def test_the_report_carries_its_evidence_base(caplog) -> None:  # noqa: ANN001
    """A reader cannot weigh "15 of 79 never invoked" without knowing how much
    history that stands on. The observed-tool count is the denominator, and it
    was absent — which is how the same list printed at every boot for days
    without ever supporting the decision it exists to inform."""
    with caplog.at_level(logging.INFO, logger="stackowl.tool"):
        await report_never_invoked(("alpha", "beta"), _Db(("alpha",)), scope="all")

    fields: dict = {}
    for record in caplog.records:
        if "capability usage" in record.getMessage():
            fields = dict(getattr(record, "_fields", {}) or {})
    assert fields.get("n_observed_tools") == 1, (
        f"the report states a numerator with no denominator: {fields}"
    )
    assert fields.get("n_never_invoked") == 1


async def test_a_healthy_corpus_still_reports_normally(caplog) -> None:  # noqa: ANN001
    """The guard must not swallow the real case — this is the live shape, where
    plenty of tools have been used and a few have not."""
    used = tuple(f"tool_{i}" for i in range(40))
    with caplog.at_level(logging.INFO, logger="stackowl.tool"):
        never = await report_never_invoked((*used, "evolve_now"), _Db(used), scope="all")

    assert never == ["evolve_now"]
    assert "UNKNOWN" not in caplog.text
