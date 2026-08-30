"""Log retention must be able to FIND the files it named.

MEASURED 2026-08-22, on the live box: `~/.stackowl/logs` was 772 MB across 31
files reaching back to 2026-07-23, with `backupCount=30` configured the whole
time. `getFilesToDelete()` returned 0 of 5 rotated files in a direct probe — so
retention had never deleted anything and the directory grew without bound.

The cause is a split pair. `setup_logging` installs a custom `namer` so rotated
files are `stackowl-2026-07-23.jsonl`, which is what `read_logs`, `trace_cli` and
every operator habit globs for. The stdlib's deletion pass ignores the namer and
keeps only names starting with `baseFilename + "."` — `stackowl.jsonl.` — which no
named file can ever match. Both halves were individually correct; nobody owned the
pair. A write with no reader, wearing a retention uniform.

These tests drive the HANDLER, not a helper, because a helper-level test would
have passed against the broken code just as happily.
"""

from __future__ import annotations

from pathlib import Path

from stackowl.infra.observability import DailyJsonlRotatingFileHandler


def _handler(log_dir: Path, *, keep: int) -> DailyJsonlRotatingFileHandler:
    handler = DailyJsonlRotatingFileHandler(
        filename=str(log_dir / "stackowl.jsonl"),
        when="midnight", utc=True, backupCount=keep, encoding="utf-8",
    )
    handler.close()
    return handler


def _seed(log_dir: Path, days: list[str]) -> None:
    for day in days:
        (log_dir / f"stackowl-{day}.jsonl").write_text("{}\n")


DAYS = [f"2026-08-{d:02d}" for d in range(1, 11)]  # 10 rotated days


def test_it_finds_the_files_the_namer_actually_wrote(tmp_path: Path) -> None:
    """THE BUG. The stdlib found 0 of 5; this must find every file beyond the cap."""
    _seed(tmp_path, DAYS)
    handler = _handler(tmp_path, keep=7)

    doomed = handler.getFilesToDelete()

    assert len(doomed) == 3, (
        f"10 rotated days with backupCount=7 must yield 3 deletions, got "
        f"{len(doomed)}: {doomed}. Zero here is the original defect — retention "
        "configured, and unable to match a single one of its own filenames."
    )


def test_it_deletes_the_OLDEST_days_not_an_arbitrary_three(tmp_path: Path) -> None:
    """Retention that drops the wrong end keeps the bytes and loses the history."""
    _seed(tmp_path, DAYS)
    handler = _handler(tmp_path, keep=7)

    names = {Path(p).name for p in handler.getFilesToDelete()}

    assert names == {
        "stackowl-2026-08-01.jsonl",
        "stackowl-2026-08-02.jsonl",
        "stackowl-2026-08-03.jsonl",
    }, names


def test_the_LIVE_log_is_never_a_deletion_candidate(tmp_path: Path) -> None:
    """`stackowl.jsonl` is the file being written to right now.

    It carries no date segment, so it cannot match — asserted rather than assumed,
    because deleting the open handle would take logging out silently and the next
    thing anyone would notice is the absence of evidence.
    """
    _seed(tmp_path, DAYS)
    (tmp_path / "stackowl.jsonl").write_text("live\n")
    handler = _handler(tmp_path, keep=7)

    assert all(Path(p).name != "stackowl.jsonl" for p in handler.getFilesToDelete())


def test_it_leaves_unrelated_files_alone(tmp_path: Path) -> None:
    """The log directory is shared — `start_cron.log` lives there on the live box.

    A retention sweep that globs too widely deletes someone else's data, which is
    strictly worse than the leak it fixes.
    """
    _seed(tmp_path, DAYS)
    (tmp_path / "start_cron.log").write_text("x")
    (tmp_path / "stackowl-notes.jsonl").write_text("x")  # no date — not ours
    handler = _handler(tmp_path, keep=7)

    names = {Path(p).name for p in handler.getFilesToDelete()}
    assert "start_cron.log" not in names
    assert "stackowl-notes.jsonl" not in names


def test_under_the_cap_nothing_is_deleted(tmp_path: Path) -> None:
    """The other jaw — a young install must not lose the little history it has."""
    _seed(tmp_path, DAYS[:5])
    assert _handler(tmp_path, keep=7).getFilesToDelete() == []


def test_a_zero_or_negative_cap_deletes_nothing(tmp_path: Path) -> None:
    """`STACKOWL_LOG_RETAIN_DAYS=0` is the operator asking to keep everything.

    Reading it as "keep zero days" would wipe the logs on the next midnight — the
    single most destructive misreading available here, so it is pinned.
    """
    _seed(tmp_path, DAYS)
    assert _handler(tmp_path, keep=0).getFilesToDelete() == []


def test_the_DEFAULT_retention_is_30_days() -> None:
    """Raised from 7 to 30 by Bakir on 2026-08-30, and pinned because retention IS
    this programme's evidence horizon.

    Acceptance checks here are largely closing queries over the rotated logs. On
    2026-08-30 five dated files were deleted at 16:22 leaving two, and three claims
    measured that morning — D08.3's "210 nudges", D05.8's "869 cap breaches",
    ESC-69's "414 searches" — became unreproducible within hours of being written
    down. A silent drop back to 7 would do that again, so the number is asserted
    rather than trusted.

    His earlier choice of 7 (2026-08-22) is not contradicted: it was made when the
    root disk had 1.3 GB free, and it now has 20 GB.
    """
    import inspect
    import re

    from stackowl.infra import observability

    src = inspect.getsource(observability.setup_logging)
    match = re.search(r'STACKOWL_LOG_RETAIN_DAYS["\']\s*,\s*["\'](\d+)["\']', src)
    assert match, "the retention default is no longer read from STACKOWL_LOG_RETAIN_DAYS"
    assert match.group(1) == "30", f"retention default is {match.group(1)}, expected 30"
