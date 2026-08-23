"""D09.5 — the way out of the dropped tail must name a tool that can enumerate.

MEASURED 2026-08-23. The injected skill catalogue is capped at
``_DEFAULT_CAP = 4000`` characters while 160 skills are enabled, so it truncates
on essentially every turn: 2,460 ``skill injection: catalog truncated by budget``
records across the retained 9-day window, carrying ``dropped: 146``-``149``.
Reproducing the render against the live store put the visible set at roughly a
dozen skills, cut alphabetically around the letter "c".

The only pointer out of that tail read::

    (+148 more — skill_view to list)

``skill_view`` is precisely the tool that cannot list. Its schema is
``{"required": ["name"]}`` (tools/knowledge/skill_view.py) and its own not-found
message says *"Use skills_list to see available skills"*. So the model was told
to enumerate with a tool that loads one item by exact name — and a name in the
dropped tail is, by construction, a name it has not been shown.

``presentation.py:81`` already states the intended pairing: both tools are in the
guaranteed always-present set so an owl can "always find (skills_list) and load
(skill_view) an installed skill". The catalogue line contradicted its own design
one file away.

The symptom this predicts, and which the log confirms: over nine days
``skills_list`` ran twice and ``skill_view`` seventeen times — and every name
``skill_view`` was called with sorted inside the visible window or was an owl-DNA
skill the caller already knew. Nothing ever reached into the dropped tail.

These tests pin the pointer and the diagnosability of the cut. They deliberately
do NOT assert a particular cap, ordering or dropped count — what to show and in
what order is a live design question (see the escalations for D09.5); that the
escape hatch works, and that the record says where the cut fell, is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from stackowl.skills.instruction_injector import (
    SkillInstructionInjector,
    SkillTier,
)


@dataclass
class _Sk:
    name: str
    description: str = "does a thing"
    when_to_use: str = "when a thing must be done"
    source: str = "builtin"


def _render_with_overflow(caplog: pytest.LogCaptureFixture) -> str:
    """Render far more skills than any sane budget can hold."""
    injector = SkillInstructionInjector()
    many = [_Sk(name=f"skill-{i:03d}-with-a-deliberately-long-name") for i in range(400)]
    tiered = [(sk, SkillTier.SUMMARY, False) for sk in many]
    with caplog.at_level(logging.WARNING):
        return injector.render("test-owl", tiered)


def test_the_escape_hatch_names_skills_list_not_skill_view(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE BUG. skill_view cannot enumerate; skills_list is the enumerator."""
    out = _render_with_overflow(caplog)
    assert "more" in out, "this fixture must actually overflow the budget"
    assert "skills_list" in out, (
        "the only route out of the dropped tail must name the tool that lists"
    )
    assert "skill_view to list" not in out, (
        "skill_view requires an exact name — it cannot list anything"
    )


def test_the_truncation_record_says_where_the_cut_fell(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A count alone cannot say which capability went missing.

    Selection is by name order, so the boundary is the fact worth recording —
    the same lesson as D05.8's ``dropped[:20]``, where a truncated field was
    read as a complete answer.
    """
    _render_with_overflow(caplog)
    recs = [r for r in caplog.records if "catalog truncated" in r.getMessage()]
    assert recs, "the truncation must still be reported"
    fields = dict(getattr(recs[-1], "_fields", {}) or {})
    assert fields.get("dropped", 0) > 0
    assert "presented" in fields, "how many DID reach the model"
    assert fields.get("last_presented"), "the last name that made the cut"
    assert fields.get("first_dropped"), "the first name that did not"
    assert fields["last_presented"] != fields["first_dropped"]


def test_the_truncation_record_is_at_WARNING_so_it_exists_in_production() -> None:
    """Production runs at INFO; a debug line is not evidence.

    This one already was at WARNING, which is why the outage was findable at
    all. Pinned so it stays that way.
    """
    injector = SkillInstructionInjector()
    many = [(_Sk(name=f"s-{i:03d}-long-enough-to-overflow"), SkillTier.SUMMARY, False)
            for i in range(400)]
    caplog_level = logging.WARNING
    logger = logging.getLogger("stackowl.engine")
    records: list[logging.LogRecord] = []

    class _Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    h = _Grab(level=caplog_level)
    logger.addHandler(h)
    try:
        injector.render("test-owl", many)
    finally:
        logger.removeHandler(h)

    hits = [r for r in records if "catalog truncated" in r.getMessage()]
    assert hits and all(r.levelno >= logging.WARNING for r in hits)


def test_no_truncation_line_and_no_pointer_when_everything_fits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The pointer must not appear when there is nothing to point at."""
    injector = SkillInstructionInjector()
    tiered = [(_Sk(name="only-one"), SkillTier.SUMMARY, False)]
    with caplog.at_level(logging.WARNING):
        out = injector.render("test-owl", tiered)
    assert "more —" not in out
    assert not [r for r in caplog.records if "catalog truncated" in r.getMessage()]
