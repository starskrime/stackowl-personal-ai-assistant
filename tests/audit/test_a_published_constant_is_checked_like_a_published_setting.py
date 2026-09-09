"""The published-default detector could not see a module constant, and said "none".

MEASURED 2026-09-09, chasing D03.2 — the compaction item, and the one the operator
reported as a live defect. Its Configuration table published `_HISTORY_BUDGET_TOKENS`,
`classify.py`, **12,000** as the trigger for compressing a conversation. Three things
were wrong at once and nothing reported any of them:

* **No such symbol exists anywhere in `src/`.** The real constant is
  `HISTORY_BUDGET_TOKENS`, no leading underscore.
* **It is in a different module** — `memory/conversation_compressor.py`, not `classify.py`.
* **The value stopped being the trigger.** DEBT-248 made the budget a share of the
  RESOLVED WINDOW (196,608 here); 12,000 survives only as the fallback for a window that
  cannot be resolved. 12,000 is 4.6% of this window and is precisely the number the
  operator reported.

WHY IT WAS INVISIBLE, and it is a denominator failure inside the denominator report.
`_CONFIG_KEY` requires a DOTTED lowercase `section.field`, because the live value came
from a `Settings` model and that is how a Settings field is spelled. Every row keyed on a
MODULE CONSTANT matched nothing and was skipped in silence — not `gone`, not counted,
never seen. The report then printed *"none, across 16 config row(s)"*, which is a clean
verdict over the 16 it could parse and says nothing about the 23 it could not. **A
detector that drops the rows it cannot parse reports a clean corpus and an incomplete one
identically** — the exact error its own denominator line exists to prevent, one level in.

And the blind class is the WORSE one. A module constant is what "retired means deleted"
has already been burned by twice: D05.7 advertised `hard_stop_enabled` for eight days
after ESC-68 deleted it.

THREE INSTRUMENT DEFECTS WERE FOUND BY VERIFYING THE FIRST RUN RATHER THAN BELIEVING IT,
and each is pinned below. The first run reported eleven findings; four were artefacts.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
from doc_check import (  # noqa: E402
    _all_src_constants,
    _claimed_literals,
    _value_agrees,
    config_claims,
    module_constants,
)


class TestTheWalkSeesEveryFormAConstantIsWrittenIn:
    @pytest.mark.tripwire
    def test_an_annotated_constant_is_not_reported_as_deleted(self) -> None:
        """THE MANUFACTURED DELETION, and it nearly shipped. The first walk handled only
        `ast.Assign`, so `DEFAULT_HOOK_TIMEOUT_SECONDS: Final = 2.0` and three siblings
        read as "exists NOWHERE in src/" — under a rule whose entire point is that a
        deletion left something behind. A raw-grep control caught it; absence is exactly
        the claim nobody re-checks, so a detector that reports it from an incomplete walk
        is worse than no detector."""
        found = module_constants(["src/stackowl/plugins/hooks.py"])

        assert found.get("DEFAULT_HOOK_TIMEOUT_SECONDS") == [
            ("src/stackowl/plugins/hooks.py", 2.0)
        ]
        assert found.get("DEFAULT_MAX_CONSECUTIVE_FAILURES") == [
            ("src/stackowl/plugins/hooks.py", 3)
        ]

    @pytest.mark.tripwire
    def test_a_computed_constant_EXISTS_even_though_its_value_cannot_be_read(self) -> None:
        """`_VALID_SOURCES: tuple[...] = get_args(SkillSource)` is live and statically
        unresolvable. Dropping it would put a live name back in the "nowhere in src"
        bucket, so it is recorded with a sentinel and its VALUE simply not asserted."""
        found = module_constants(["src/stackowl/skills/loader.py"])

        assert "_VALID_SOURCES" in found, "a computed constant read as absent"
        assert repr(found["_VALID_SOURCES"][0][1]) == "<computed at import time>"

    @pytest.mark.tripwire
    def test_a_symbol_that_really_is_gone_is_still_reported(self) -> None:
        """The control in the other direction. Widening the walk until nothing is ever
        `gone` would be a guard that cannot fail — and these two names are the reason the
        bucket exists."""
        every = _all_src_constants()

        assert "_HISTORY_BUDGET_TOKENS" not in every
        assert "_DEEP_HISTORY_TURNS" not in every
        assert "HISTORY_BUDGET_TOKENS" in every, "the real symbol is missing too — the walk is broken"
        assert "DEEP_HISTORY_TURNS" in every


class TestTheComparisonReadsWhatTheCorpusWrites:
    @pytest.mark.tripwire
    def test_a_thousands_separator_is_not_a_token_boundary(self) -> None:
        """`4,000` split into `4` and `000`, the leading token became the claim, and a
        CORRECT row was reported as "says 4, live 4000"."""
        assert _claimed_literals("4,000") == ["4000"]
        assert _value_agrees("12,000", 12000)

    @pytest.mark.tripwire
    def test_an_escaped_whitespace_value_is_not_a_disagreement(self) -> None:
        """Three defects in one row. A table can only write `\\n`; the live value is a
        real newline — and every other comparison here calls `str(live).strip()`, which
        is right for a number and MUTILATES a value that IS whitespace. `ENTRY_DELIMITER`
        stripped is `§`."""
        assert _value_agrees(r'"\n§\n"', "\n§\n")
        assert not _value_agrees(r'"\n#\n"', "\n§\n"), "a real disagreement must still fail"

    def test_a_number_comparison_is_unaffected_by_any_of_it(self) -> None:
        assert _value_agrees("40", 40) and not _value_agrees("41", 40)


class TestTheReportIsHonestAboutWhatItLookedAt:
    @pytest.mark.tripwire
    def test_the_denominator_now_includes_constant_rows(self) -> None:
        """16 of 39 was the old coverage. A denominator that silently excludes the rows
        most likely to be wrong is the failure this report exists to prevent."""
        from stackowl.config.settings import Settings

        res = config_claims(Settings())
        total = sum(len(v) for v in res.values())

        assert total >= 35, f"the walk sees only {total} rows; it used to see 16 of 39"
        assert res["gone"] == [], [f"{c.doc}:{c.line} {c.key}" for c in res["gone"]]

    @pytest.mark.tripwire
    def test_a_citation_gap_is_NOT_reported_as_a_deleted_symbol(self) -> None:
        """D05.4 publishes `HARD_TOOL_COUNT_CAP`, which lives in a module D05.4 does not
        cite. "Not in this document's sources" and "not anywhere in src" are different
        findings and only the second is a stale row; conflating them cries wolf on a
        correct one. The value stays scoped to the cited sources deliberately —
        `_DEFAULT_CAP` is 38 in D05.8 and 4,000 in D10.6, two real constants in two
        modules, and a repo-wide resolve would mark one false on the other's value."""
        from stackowl.config.settings import Settings

        res = config_claims(Settings())
        uncited = {c.key for c in res["uncited"]}

        assert "HARD_TOOL_COUNT_CAP" in uncited
        assert "HARD_TOOL_COUNT_CAP" not in {c.key for c in res["gone"]}

    def test_the_report_runs_and_its_headline_matches_its_list(self) -> None:
        """The headline counted two buckets and printed four — "6 of 39" over nine listed
        rows. A header disagreeing with its own list is the defect class this whole report
        is about."""
        out = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "doc_check.py")],
            capture_output=True, text=True, timeout=900, cwd=_ROOT,
        )
        header = [ln for ln in out.stdout.splitlines() if "PUBLISHED DEFAULT" in ln]
        assert len(header) == 1, out.stdout[-600:]
        if "none," in header[0]:
            return
        listed = 0
        for ln in out.stdout.split(header[0], 1)[1].splitlines():
            if ln.startswith("  ") and "  —  " in ln:
                listed += 1
            elif ln.strip() and not ln.startswith(" "):
                break
        claimed = int(header[0].split("—")[1].strip().split()[0])
        assert claimed == listed, (claimed, listed, header[0])
