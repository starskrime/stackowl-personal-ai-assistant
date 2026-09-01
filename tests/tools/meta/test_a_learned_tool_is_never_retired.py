"""The platform can create tools and has no way to retire them.

MEASURED 2026-09-01, the asymmetry stated plainly:

* a SKILL carries ``lifecycle_state``, ``last_used_at``, ``n_executions``,
  ``enabled`` and ``pinned``, and can be archived, pruned and revived;
* a LEARNED TOOL spec carries ``action_severity, argv_template, description,
  name, params, spec_version, timeout_sec`` — **nothing that records whether it
  has ever been invoked.**

The consequence is already on disk. ``run_claude_code_demo`` has been invoked
ZERO times in the whole retained history, shells out to flags that do not match
the CLI it names, occupies a slot in the presented tool set, and trips a "thin
tool description" WARNING on every single boot — 43 of them, the last at
10:49:35. It will keep doing that forever, because nothing ever looks.

IT MATTERS NOW RATHER THAN EVENTUALLY. The ``tool_build`` consent path was
verified working earlier today (it had been 0-for-20), so the RCA loop is about
to start creating tools into an append-only registry.

NOTHING IS DELETED HERE, and that is deliberate: retiring a learned capability is
the operator's call, not a loader's. What was missing is that the accumulation
was INVISIBLE. Usage is derived from ``task_outcomes.tool_sequence``, which the
platform already records — no second store, no new field on the spec.

AN UNKNOWN MUST NOT READ AS "NONE". With no pool the loader says usage is
unknown rather than reporting zero never-invoked tools, because a silent zero is
how this project has repeatedly mistaken ignorance for a clean bill of health.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.tools.meta.learned_tool_loader import LearnedToolLoader

pytestmark = pytest.mark.asyncio


class _Db:
    """Answers the usage query with a fixed set of tools that HAVE run."""

    def __init__(self, used: tuple[str, ...]) -> None:
        self._used = used

    async def fetch_all(self, sql: str, *args: object) -> list[dict]:
        assert "tool_sequence" in sql, "usage must come from the recorded sequences"
        return [{"tool": t} for t in self._used]


class _AngryDb:
    async def fetch_all(self, *a: object, **k: object) -> list[dict]:
        raise RuntimeError("no such table")


async def test_a_never_invoked_tool_is_named(caplog) -> None:  # noqa: ANN001
    """The live case: run_claude_code_demo, zero invocations, forever."""
    loader = LearnedToolLoader()
    with caplog.at_level(logging.INFO):
        await loader._report_unused(  # noqa: SLF001
            ["run_claude_code_demo", "instagram_media_extractor"],
            _Db(used=("instagram_media_extractor", "shell")),
        )
    rec = [r for r in caplog.records if "learned-tool usage" in r.getMessage()]
    assert rec, "the loader said nothing about learned-tool usage"
    fields = rec[0]._fields  # noqa: SLF001
    assert fields["never_invoked"] == ["run_claude_code_demo"]
    assert fields["n_never_invoked"] == 1


async def test_a_used_tool_is_not_named(caplog) -> None:  # noqa: ANN001
    """The expensive direction: naming a working tool as dead invites someone to
    delete a capability that is in use."""
    with caplog.at_level(logging.INFO):
        await LearnedToolLoader()._report_unused(  # noqa: SLF001
            ["instagram_media_extractor"], _Db(used=("instagram_media_extractor",)),
        )
    rec = [r for r in caplog.records if "learned-tool usage" in r.getMessage()]
    assert rec[0]._fields["never_invoked"] == []  # noqa: SLF001


async def test_no_pool_reports_UNKNOWN_not_zero(caplog) -> None:  # noqa: ANN001
    """An unknown must never read as "none" — that mistake is why an
    append-only registry went unnoticed in the first place."""
    with caplog.at_level(logging.INFO):
        await LearnedToolLoader()._report_unused(["x"], None)  # noqa: SLF001
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "UNKNOWN" in msg
    assert "learned-tool usage" not in msg, (
        "a pool-less loader reported a usage verdict it could not have"
    )


async def test_a_broken_read_reports_nothing_rather_than_a_false_zero(caplog) -> None:  # noqa: ANN001
    with caplog.at_level(logging.WARNING):
        await LearnedToolLoader()._report_unused(["x"], _AngryDb())  # noqa: SLF001
    assert any("false zero" in r.getMessage() for r in caplog.records)


async def test_no_learned_tools_says_nothing_at_all(caplog) -> None:  # noqa: ANN001
    """A platform with no learned tools must not emit a daily line about it."""
    with caplog.at_level(logging.INFO):
        await LearnedToolLoader()._report_unused([], _Db(used=()))  # noqa: SLF001
    assert not [r for r in caplog.records if "learned-tool" in r.getMessage()]


async def test_reporting_never_costs_the_boot() -> None:
    """The loader's whole contract is that one bad file cannot wedge startup;
    a usage REPORT must not be the thing that does."""
    await LearnedToolLoader()._report_unused(["x"], object())  # noqa: SLF001


def test_the_spec_still_has_no_lifecycle_field() -> None:
    """The finding itself, pinned. If a lifecycle field is ever added to the
    spec, this reporting becomes the wrong mechanism and should be replaced by
    it rather than left alongside — two sources for one fact is the shape this
    codebase keeps paying for."""
    from stackowl.tools.meta.tool_build import LearnedToolSpec

    fields = set(LearnedToolSpec.model_fields)
    assert not (fields & {"last_used_at", "n_executions", "lifecycle_state"}), (
        "a learned tool now records its own usage — derive from the spec "
        "instead of from task_outcomes, and delete _report_unused's query"
    )
