"""A closing check must count evidence produced AFTER the change it validates.

WHY THIS EXISTS, and it was found by the mechanism it protects, on that mechanism's
first real use — one day after it shipped.

`validate_check.py` re-runs a `closing_check` for every stage recorded `partial`. Every
log-based check written on 2026-09-06 counted matches across ALL retained logs with no
lower bound. On the first run D07.2 reported **CLOSEABLE** on 4 hits of
`depth>0 child denied spawn/delegate tool`.

All four of those hits are dated **2026-08-28**. The code they were supposedly evidencing
shipped on **2026-09-04**, seven days later (commit 8750d046). So the check answered "yes,
this works in production" using events that predate the work entirely, and had it been
believed, an item would have been advanced to done on evidence of something else.

That is the honest-validate rule's own named failure — "a fix that worked in tests and
never fired in production" — reached by a different road. It is also CLAUDE.md's
denominator rule one level up: checking that a number is non-zero is not checking what the
number is MADE OF, and a raw count over a rotating log is made of whatever is still on disk.

THE FIX IS STRUCTURAL, not seven edited strings. `scripts/log_since.sh <date> <pattern>`
takes the window as a required argument, so a log-based check cannot be written without
stating the day its subject shipped. It also carries two instrument lessons in one place —
`grep -a`, because the logs hold null bytes and without it a present pattern counts 0; and
a stderr warning when the requested date predates the oldest retained log, since a 0 then
means "rotated away" rather than "never happened".
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(_ROOT / "scripts"))
from progress_lint import entries_with_closing_checks as _record_checks  # noqa: E402
_SCRIPT = _ROOT / "scripts" / "log_since.sh"


def _run(tmp_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_SCRIPT), *args],
        cwd=_ROOT, capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_dir), "STACKOWL_LOG_DIR": str(tmp_dir)},
    )


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    """Three stamped days plus the current file — the real shape on disk."""
    (tmp_path / "stackowl-2026-08-28.jsonl").write_text('{"msg": "TARGET old"}\n')
    (tmp_path / "stackowl-2026-09-04.jsonl").write_text('{"msg": "TARGET shipday"}\n')
    (tmp_path / "stackowl-2026-09-05.jsonl").write_text('{"msg": "TARGET after"}\n')
    (tmp_path / "stackowl.jsonl").write_text('{"msg": "TARGET current"}\n')
    return tmp_path


def _command_only(check: str) -> str:
    """The check with its explanatory comments removed.

    THREE GUARDS IN THIS FILE READ THE COMMENT INSTEAD OF THE COMMAND before this
    existed, and the third was found by a mutant that SURVIVED: a check reverted to a
    raw `grep ~/.stackowl/logs/stackowl.jsonl` was reported as compliant, because the
    comment above it still said the words "log_since.sh". A guard satisfied by prose
    describing the rule is worse than no guard — the prose is exactly what a careless
    edit leaves behind.

    Same family as the tripwire that matched the comment explaining why it must not, and
    as counting "429" in a log and hitting token counts. Ask the COMMAND.
    """
    return "\n".join(
        ln for ln in check.splitlines() if not ln.lstrip().startswith("#")
    )


class TestTheWindow:
    def test_it_excludes_evidence_older_than_the_change(self, logs: Path) -> None:
        """THE D07.2 CASE EXACTLY. Four hits exist; none of them count, because all
        of them predate the ship date."""
        assert _run(logs, "2026-09-04", "TARGET").stdout.strip() == "3"

    def test_an_unbounded_question_would_have_counted_all_of_them(self, logs: Path) -> None:
        """The control that makes the number above mean something. Without it, "3"
        could be three of anything — a filter that filters nothing looks identical to
        one that filters correctly when you only ever run it one way."""
        assert _run(logs, "2026-08-28", "TARGET").stdout.strip() == "4"

    def test_the_CURRENT_file_is_always_in_the_window(self, tmp_path: Path) -> None:
        """`stackowl.jsonl` carries today's lines and has no date in its name.
        Dropping it would blind every check to evidence produced minutes ago — the
        most likely moment for a closing check to finally succeed."""
        (tmp_path / "stackowl-2026-09-04.jsonl").write_text("nothing here\n")
        (tmp_path / "stackowl.jsonl").write_text('{"msg": "TARGET just now"}\n')

        assert _run(tmp_path, "2026-09-04", "TARGET").stdout.strip() == "1"

    def test_no_logs_at_all_is_zero_not_a_crash(self, tmp_path: Path) -> None:
        assert _run(tmp_path, "2026-09-04", "TARGET").stdout.strip() == "0"


class TestTheInstrumentLessons:
    def test_a_null_byte_does_not_hide_a_match(self, tmp_path: Path) -> None:
        """MEASURED 2026-09-06: the live logs contain null bytes, and without `grep -a`
        a pattern present in the file AND present in src counted 0 across all ten daily
        files. The control that exposed it was a second pattern known to be present."""
        (tmp_path / "stackowl-2026-09-05.jsonl").write_bytes(
            b'{"msg": "noise \x00 binary"}\n{"msg": "TARGET after the null"}\n'
        )

        assert _run(tmp_path, "2026-09-04", "TARGET").stdout.strip() == "1"

    def test_a_truncated_window_is_ANNOUNCED(self, logs: Path) -> None:
        """A zero from a window shorter than the one asked for means "rotated away",
        not "never happened". Absorbing that difference silently is the exact mistake
        this file exists about."""
        out = _run(logs, "2026-01-01", "TARGET")

        assert "WINDOW TRUNCATED" in out.stderr
        assert out.stdout.strip() == "4", "it must still answer, not just warn"

    def test_a_full_window_warns_about_NOTHING(self, logs: Path) -> None:
        """The control. A warning that always fires is a warning nobody reads."""
        assert "WINDOW TRUNCATED" not in _run(logs, "2026-09-04", "TARGET").stderr

    def test_a_malformed_date_is_refused(self, logs: Path) -> None:
        """Fails loudly rather than treating an unparseable window as 'everything'."""
        out = _run(logs, "last-tuesday", "TARGET")

        assert out.returncode != 0
        assert "not YYYY-MM-DD" in out.stderr


class TestEveryLiveCheckUsesIt:
    @pytest.mark.tripwire
    def test_no_closing_check_greps_the_logs_unbounded(self) -> None:
        """THE GUARD THAT MAKES THE FIX STICK, and the reason it is structural rather
        than seven corrected strings. A log-based check written the obvious way — a
        `grep` over `stackowl*.jsonl` — is unbounded by construction and will be
        satisfied by history the moment the window contains anything older than the
        change. `log_since.sh` takes the date as a REQUIRED argument, so routing every
        such check through it is what makes the window impossible to forget.
        """
        data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
        offenders: list[str] = []
        for ident, raw in _record_checks(data):
            check = _command_only(raw)
            if not check.strip():
                continue
            reads_logs = "stackowl.jsonl" in check or "stackowl*.jsonl" in check
            if reads_logs and "log_since.sh" not in check:
                offenders.append(ident)
        assert not offenders, (
            "these closing checks read the logs without a ship-date bound, so they can "
            f"report CLOSEABLE on evidence older than the change: {offenders}"
        )

    @pytest.mark.tripwire
    def test_every_log_since_call_names_a_real_date(self) -> None:
        """A date the script would reject makes the check answer 0 forever while
        looking healthy — an OPEN that can never become CLOSEABLE."""
        data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
        bad: list[str] = []
        for ident, raw in _record_checks(data):
            code = _command_only(raw)
            for date in re.findall(r"log_since\.sh\s+(\S+)", code):
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    bad.append(f"{ident}: {date!r}")
        assert not bad, "\n  ".join(bad)
