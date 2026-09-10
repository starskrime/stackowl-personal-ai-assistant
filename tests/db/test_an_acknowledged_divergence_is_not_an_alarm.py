"""A settled question must stop being asked at the severity of an open one.

MEASURED 2026-09-10 over every retained log. `0137` — an applied migration whose
file is not in the tree — produced **426** WARNING records across two lines per
boot, and in the current window it is **172 of 703** WARNING/ERROR records: **24%
of the operator's entire alarm channel**, for a question this repo examined and
settled on 2026-09-05.

THE COST IS NOT THE VOLUME, IT IS THE DETECTOR. A channel where a quarter of the
traffic is a known non-event is one where the next REAL mismatch arrives looking
exactly like the noise. This loop reads that channel every invocation to find
defects; three separate signals had to be checked and discarded as already-settled
before this one was reached.

THE CAUSE IS A MISSING DISTINCTION. `_verify_applied` had ONE verdict for two
different facts — a divergence nobody has explained, and one already examined and
accepted — so it re-raised the settled one on every boot, at the severity of the
real thing. The knowledge that 0137 is accepted existed in the tree the whole
time: D18.9 explains it and
`test_a_migration_number_the_ledger_already_claims_is_never_reused` reserves the
number. **The code that checks it on every boot was simply never told** — one rule,
three places, and the enforcing copy is the one that was missed.

WHAT IS NOT CHANGED, deliberately: an UNACKNOWLEDGED divergence still warns, in
the same words, at the same level. The all-clear still names the acknowledged row.
Nothing is hidden; a severity is corrected to match a fact.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from stackowl.db.migrations import runner as runner_mod
from stackowl.db.migrations.runner import MigrationRunner, semantic_checksum

_SQL = "CREATE TABLE t (a);\n"


def _tree(tmp_path: Path, versions: list[str]) -> Path:
    d = tmp_path / "migrations"
    d.mkdir()
    for version in versions:
        (d / f"{version}_thing.sql").write_text(_SQL, encoding="utf-8")
    return d


def _applied(db: Path, rows: list[tuple[str, str, str | None]]) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version TEXT PRIMARY KEY, name TEXT, applied_at TEXT,"
        "  checksum TEXT, sql_checksum TEXT)"
    )
    conn.executemany(
        "INSERT OR REPLACE INTO schema_migrations "
        "(version, name, applied_at, checksum, sql_checksum) VALUES (?,?,'now','x',?)",
        rows,
    )
    conn.commit()
    conn.close()


def _verify(db: Path, mig_dir: Path, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)
    runner = MigrationRunner(db, mig_dir)
    files = [
        (f.name.split("_")[0], f.name, f) for f in sorted(Path(mig_dir).glob("*.sql"))
    ]
    runner._verify_applied(files)  # noqa: SLF001 — the pass under test
    return [r for r in caplog.records if "runner.verify" in r.getMessage()]


@pytest.mark.tripwire
def test_an_acknowledged_missing_file_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 24% — reported, at the level a settled question deserves."""
    monkeypatch.setattr(runner_mod, "ACKNOWLEDGED_MISSING", {"0003": "withdrawn, see D18.9"})
    mig = _tree(tmp_path, ["0001", "0002"])
    db = tmp_path / "x.db"
    _applied(db, [
        ("0001", "0001_thing.sql", semantic_checksum(_SQL)),
        ("0002", "0002_thing.sql", semantic_checksum(_SQL)),
        ("0003", "0003_gone.sql", "whatever"),
    ])

    records = _verify(db, mig, caplog)
    warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
    assert not warnings, f"an acknowledged divergence still warns: {warnings}"

    said = [r.getMessage() for r in records if "ACKNOWLEDGED" in r.getMessage()]
    assert said, "it went quiet instead of explaining — that is hiding, not classifying"
    assert "withdrawn, see D18.9" in said[0], said[0]


@pytest.mark.tripwire
def test_an_UNacknowledged_missing_file_still_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE CONTROL, and the whole reason this is a distinction and not a mute.

    If this passed while the test above also passed only because the branch had
    been silenced, the integrity check would be decoration.
    """
    monkeypatch.setattr(runner_mod, "ACKNOWLEDGED_MISSING", {})
    mig = _tree(tmp_path, ["0001"])
    db = tmp_path / "x.db"
    _applied(db, [
        ("0001", "0001_thing.sql", semantic_checksum(_SQL)),
        ("0003", "0003_gone.sql", "whatever"),
    ])

    records = _verify(db, mig, caplog)
    warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
    assert any("no file in the tree" in m for m in warnings), warnings
    assert any("NOT\nfully checked" in m or "not\nfully checked" in m.lower()
               or "unverifiable" in m for m in warnings), warnings


@pytest.mark.tripwire
def test_the_summary_still_names_the_acknowledged_row(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reported, never hidden. A reader must still learn the ledger holds one."""
    monkeypatch.setattr(runner_mod, "ACKNOWLEDGED_MISSING", {"0003": "withdrawn"})
    mig = _tree(tmp_path, ["0001"])
    db = tmp_path / "x.db"
    _applied(db, [
        ("0001", "0001_thing.sql", semantic_checksum(_SQL)),
        ("0003", "0003_gone.sql", "whatever"),
    ])

    summary = [r.getMessage() for r in _verify(db, mig, caplog) if "exit" in r.getMessage()]
    assert summary, "the pass stopped reporting a summary"
    assert "acknowledged" in summary[0], summary[0]
    assert "0003_gone.sql" in summary[0], summary[0]


@pytest.mark.tripwire
def test_an_acknowledgement_for_a_version_never_applied_is_reported_stale(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale exemption is the same defect wearing the reviewer's badge.

    Left unchecked it excuses nothing today and is still able to excuse the NEXT
    divergence that reuses the number.
    """
    monkeypatch.setattr(runner_mod, "ACKNOWLEDGED_MISSING", {"9999": "never happened"})
    mig = _tree(tmp_path, ["0001"])
    db = tmp_path / "x.db"
    _applied(db, [("0001", "0001_thing.sql", semantic_checksum(_SQL))])

    warnings = [r.getMessage() for r in _verify(db, mig, caplog) if r.levelno >= logging.WARNING]
    assert any("stale" in m and "9999" in m for m in warnings), warnings


@pytest.mark.tripwire
def test_an_acknowledgement_whose_file_came_back_is_reported_stale(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dangerous half: the file exists again, so the entry now stands ready to
    excuse a real divergence on that number."""
    monkeypatch.setattr(runner_mod, "ACKNOWLEDGED_MISSING", {"0001": "withdrawn"})
    mig = _tree(tmp_path, ["0001"])
    db = tmp_path / "x.db"
    _applied(db, [("0001", "0001_thing.sql", semantic_checksum(_SQL))])

    warnings = [r.getMessage() for r in _verify(db, mig, caplog) if r.levelno >= logging.WARNING]
    assert any("stale" in m and "IS in the tree" in m for m in warnings), warnings


@pytest.mark.tripwire
def test_the_shipped_acknowledgement_is_the_one_this_repo_examined() -> None:
    """The list may only hold entries someone actually looked at.

    Pins the shape rather than the size: every entry must carry a reason long
    enough to be a reason, so an empty-string exemption cannot be slipped in.
    """
    assert set(runner_mod.ACKNOWLEDGED_MISSING) == {"0137"}
    reason = runner_mod.ACKNOWLEDGED_MISSING["0137"]
    assert "D18.9" in reason, reason
    assert len(reason.split()) >= 12, reason
