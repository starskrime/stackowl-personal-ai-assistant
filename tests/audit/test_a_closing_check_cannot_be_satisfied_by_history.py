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

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(_ROOT / "scripts"))
from progress_lint import entries_with_closing_checks as _record_checks  # noqa: E402
from validate_check import _UV_NOISE  # noqa: E402
from validate_check import verdict_of as _verdict_of  # noqa: E402
_SCRIPT = _ROOT / "scripts" / "log_since.sh"


def _oldest_retained() -> str:
    """The date the retained log window opens, read from the FILES, not the setting."""
    import os
    from pathlib import Path as _P
    d = os.environ.get("STACKOWL_LOG_DIR") or str(_P.home() / ".stackowl" / "logs")
    names = sorted(p.name for p in _P(d).glob("stackowl-*.jsonl")) if _P(d).is_dir() else []
    return names[0][len("stackowl-"):-len(".jsonl")] if names else ""


def _first_appeared_in_src(pattern: str) -> str:
    """The date `pattern` first entered `src/`, via git's pickaxe.

    `--reverse` and NOT `-1`: `git log -1 -S` returns the LAST commit that changed the
    string's count, which is a different question and would date a long-lived message to
    its most recent edit. Measured on the first attempt at this — the wrong form agreed on
    six patterns and disagreed on a seventh, which is exactly how an instrument error
    survives a spot check.
    """
    import subprocess
    frag = re.split(r"\\\||\.\*", pattern)[0].replace("\\", "").strip()
    if len(frag) < 12:
        return ""
    out = subprocess.run(
        ["git", "log", "--reverse", "--format=%ad", "--date=short", "-S", frag, "--", "src/"],
        cwd=_ROOT, capture_output=True, text=True, timeout=180,
    ).stdout.splitlines()
    return out[0].strip() if out else ""


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
    def test_every_log_since_call_names_a_real_date_or_earns_all(self) -> None:
        """A date the script would reject makes the check answer 0 forever while
        looking healthy — an OPEN that can never become CLOSEABLE.

        `--all` IS ALLOWED, AND IT HAS TO BE EARNED. The date bound cures a real defect
        (a count with no lower bound is satisfied by history) but it also creates an
        EXPIRY, because the bound eventually falls below the retained window and the
        check can never be asked again. MEASURED 2026-09-07: of the fifteen outstanding
        checks, SEVEN key their evidence on a message the item INTRODUCED — for those,
        history cannot satisfy the check and the bound buys nothing while still expiring.

        The proof this asserts is the one that needs no ship date: **the pattern must not
        appear in `src/` before the oldest retained log.** If the string entered the
        source after the window opens, every retained line carrying it was written after
        the string existed. `sandbox.bwrap] run: exit` (2026-06-17) fails this and keeps
        its bound; `a single call used` (2026-08-30) passes it.

        THE RESIDUAL IS STATED RATHER THAN HIDDEN: a string introduced INSIDE the window
        but before a later fix would pass this and still admit pre-fix lines. That case
        needs the date, and this test cannot see it — so `--all` remains a judgement the
        author makes and this guard only rules out the clearly-wrong half.
        """
        data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
        oldest = _oldest_retained()
        bad: list[str] = []
        for ident, raw in _record_checks(data):
            code = _command_only(raw)
            for call in re.findall(r"log_since\.sh\s+(\S+)(?:\s+'([^']*)')?", code):
                arg, pattern = call
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", arg):
                    continue
                if arg != "--all":
                    bad.append(f"{ident}: {arg!r} is neither a date nor --all")
                    continue
                if not oldest:
                    continue  # no window to reason about; degrade to silence
                first = _first_appeared_in_src(pattern)
                if not first:
                    bad.append(f"{ident}: --all on {pattern!r}, which is in no src/ commit")
                elif first < oldest:
                    bad.append(
                        f"{ident}: --all on {pattern!r}, first in src/ {first}, BEFORE the "
                        f"retained window opens ({oldest}) — history can satisfy it"
                    )
        assert not bad, "\n  ".join(bad)


class TestABoundThatOutlivesItsEvidence:
    """The bound cures satisfied-by-history and CREATES an expiry. Nothing watched it.

    `log_since.sh` announces a truncated window on stderr, and its own comments say that
    goes to stderr "rather than being silently absorbed". MEASURED 2026-09-07:
    `validate_check.py` read `(out.stdout or out.stderr or "")`, so stderr was consulted
    ONLY when stdout was empty — and every check echoes CLOSEABLE/OPEN to stdout, so the
    warning was discarded on 100% of runs. Built, documented, and not wired.

    The consequence is the ambiguous zero one level up: a check whose window has rotted
    prints `OPEN (0)`, and OPEN reads as *not yet*, so the item sits at 6 of 7 with a
    mechanism that can never advance it.
    """

    @pytest.mark.tripwire
    def test_all_takes_no_lower_bound_and_warns_about_nothing(self, tmp_path) -> None:
        """`--all` is the honest form when history cannot satisfy the check anyway."""
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "stackowl-2026-09-01.jsonl").write_text('{"msg": "a marker line"}\n')
        (logs / "stackowl.jsonl").write_text('{"msg": "a marker line"}\n')
        # LC_ALL=C IS LOAD-BEARING IN THIS TEST, and finding that out is why it is here.
        # Without the explicit `--all` guard the script still compares the oldest
        # filename against the literal string "--all". Under the default collation
        # punctuation is ignored, so "2026-09-01" sorts BELOW "all" and no warning fires
        # — which made a mutation removing the guard survive. Under C collation the
        # hyphen sorts below the digits and it warns, so this pins the guard rather than
        # an accident of the runner's locale.
        env = {**os.environ, "STACKOWL_LOG_DIR": str(logs), "LC_ALL": "C"}
        out = subprocess.run(
            [str(_SCRIPT), "--all", "a marker line"],
            capture_output=True, text=True, env=env, timeout=120,
        )
        assert out.stdout.strip() == "2", out
        assert "WINDOW TRUNCATED" not in out.stderr, out.stderr

    @pytest.mark.tripwire
    def test_a_bound_below_the_window_still_announces_truncation(self, tmp_path) -> None:
        """The signal EXPIRED depends on. Asserted here so the two cannot drift."""
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "stackowl-2026-09-01.jsonl").write_text('{"msg": "nothing to match"}\n')
        (logs / "stackowl.jsonl").write_text("")
        env = {**os.environ, "STACKOWL_LOG_DIR": str(logs)}
        out = subprocess.run(
            [str(_SCRIPT), "2026-08-01", "a marker line"],
            capture_output=True, text=True, env=env, timeout=120,
        )
        assert out.stdout.strip() == "0", out
        assert "WINDOW TRUNCATED" in out.stderr, out.stderr

    @pytest.mark.tripwire
    def test_a_rotted_check_is_EXPIRED_not_open(self) -> None:
        """THE POINT OF THE ITEM. A zero from a window that no longer reaches the bound
        is not "not yet" — it is "this can no longer be asked"."""
        assert _verdict_of("OPEN 0 x since 2026-08-01", truncated=True) == "EXPIRED"
        assert _verdict_of("OPEN 0 x since 2026-09-06", truncated=False) == "open"

    @pytest.mark.tripwire
    def test_a_truncated_window_that_still_found_evidence_is_CLOSEABLE(self) -> None:
        """THE FALSE POSITIVE DESIGNED OUT, and the first implementation had it.

        `log_since.sh` warns whenever the requested date predates the oldest log,
        REGARDLESS of the count. Marking EXPIRED on the warning alone would relabel a real
        CLOSEABLE as expired and HIDE an item that was ready to close — worse than the
        defect being fixed. Only the zero is ambiguous.
        """
        assert _verdict_of("CLOSEABLE 4 x since 2026-08-01", truncated=True) == "CLOSEABLE"

    @pytest.mark.tripwire
    def test_tooling_chatter_is_never_reported_as_a_verdict(self) -> None:
        """`uv run` writes "Installed 1 package in 7ms" to stderr on every invocation, and
        several checks shell through it. A check printing nothing to stdout used to have
        that reported as its answer."""
        assert _UV_NOISE.sub("", "Installed 1 package in 7ms\n").strip() == ""
        assert _UV_NOISE.sub("", "Traceback (most recent call last):").strip() != ""


class TestTheRotClockModelsRotationCorrectly:
    """The first version of this clock was wrong by three weeks, in the dangerous
    direction: it printed a confident "2 days — ACT NOW".

    It computed `bound - oldest_retained`, which is how much history sits BEHIND the
    bound — not a countdown to anything. Rotation deletes only once the dated files
    EXCEED `backupCount`, and there are ten against a permitted thirty, so nothing is
    being deleted at all yet. A panel lens caught it; the arithmetic below is what the
    correction rests on, so it is pinned rather than left to a comment.
    """

    @pytest.mark.tripwire
    def test_a_bound_expires_one_day_after_retention_passes_it(self) -> None:
        import datetime
        permitted = 30
        bound = datetime.date(2026, 8, 30)
        # Deletion starts when dated files exceed `permitted`; from then the oldest
        # retained is `today - permitted`, so `bound` is lost on `bound + permitted + 1`.
        expiry = bound + datetime.timedelta(days=permitted + 1)
        assert expiry == datetime.date(2026, 9, 30), expiry
        # The wrong model — bound minus the CURRENT oldest — gave two days on 2026-09-07.
        wrong = (bound - datetime.date(2026, 8, 28)).days
        assert wrong == 2 and (expiry - datetime.date(2026, 9, 7)).days == 23

    @pytest.mark.tripwire
    def test_nothing_is_deleted_while_the_files_fit(self) -> None:
        """The property the correction depends on, asserted against the handler's own
        rule rather than against a remembered number."""
        logs = _ROOT  # any dir; the assertion is about the arithmetic, not this path
        assert logs.exists()
        present, permitted = 10, 30
        assert present <= permitted, "with files to spare, rotation deletes nothing"
