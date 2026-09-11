"""149 ERROR sweeps in one day for a writer that had run, and worked, that morning.

`tool_heuristics` was declared PERIODIC and dated by `created_at`. Its writer, the
daily `tool_outcome_miner`, UPSERTS: all fourteen runs `completed`, `store.upsert:
stored` 35 times every morning — and once the key space saturated on 2026-09-03,
every write took the `ON CONFLICT … DO UPDATE SET` branch, which does not name
`created_at`. MEASURED 2026-09-10: `created_at` 7.59 days old, `updated_at` 0.59.
The store was being written all along; the column the check reads had stopped.

AND THE SAME DEFECT WAS FIXED ONCE ALREADY, ON THE LINE ABOVE IT IN THE REGISTRY.
The comment on `jobs` records it in full — "`last_run_at`, NOT `created_at` …
created_at was 1.2 days old while last_run_at was 22 SECONDS old, and the sweep
reported UNHEALTHY 31 times in one boot". That repair went into the one table with
the symptom. The QUESTION behind it — *does the writer move the column the check
reads?* — was never asked of the other 53 declarations, so it came back five weeks
later on a different table. These tests ask it of all of them, mechanically.

THE RULE IS DERIVED FROM THE WRITERS' OWN SQL, not from a list: a store written by
`INSERT … ON CONFLICT … DO UPDATE SET <cols>` advances exactly `<cols>` on a repeat
write, so a windowed store's clock must be one of them. MEASURED across the 29
windowed declarations: 6 have their clock in the SET, 19 are written by a plain
INSERT (where an insert-time clock is correct), 4 did not, and ZERO have a writer
the scan cannot find.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from store_clock_audit import audit  # noqa: E402

from stackowl.health.store_cadence import (  # noqa: E402
    DECLARATIONS,
    Cadence,
    StoreDeclaration,
    declaration_for,
)


def _audit() -> dict[str, list[tuple[str, str, list[str]]]]:
    return audit(_ROOT)


@pytest.mark.tripwire
def test_a_store_with_a_window_is_dated_by_a_column_its_writer_moves() -> None:
    """THE RULE. A clock outside its writer's `DO UPDATE SET` stops moving while the
    store keeps being written — so the sweep reports a healthy writer as a stopped
    one, at ERROR, on every tick. An insert-time clock is allowed only where the
    declaration SAYS WHY, so the exemption is a decision a reader can see rather
    than an accident of which column someone reached for first."""
    unexplained = []
    for table, clock, set_cols in _audit()["not_moved"]:
        decl = declaration_for(table)
        assert decl is not None, f"{table} classified but not declared"
        if not decl.clock_is_insert_time_because.strip():
            unexplained.append((table, clock, set_cols))
    assert not unexplained, (
        "these stores have a silence window and are dated by a column their own "
        "writer does not move on a repeat write, so each goes silent while it is "
        "still being written:\n  "
        + "\n  ".join(
            f"{t}: clock={c!r}, but the upsert advances {s}" for t, c, s in unexplained
        )
        + "\nEither date it by a column in that SET, or state why an insert-time "
        "clock is right in `clock_is_insert_time_because`."
    )


@pytest.mark.tripwire
def test_an_insert_time_exemption_CANNOT_OUTLIVE_ITS_CAUSE() -> None:
    """THE MIRROR, and the half an allowlist normally rots without. A declaration
    carrying a written exemption whose writer no longer needs one is a note nobody
    will re-read — the stale-allowlist shape this repo has paid for twice. So the
    reason must correspond to a live classification."""
    exempt = {d.table for d in DECLARATIONS if d.clock_is_insert_time_because.strip()}
    needs_one = {t for t, _c, _s in _audit()["not_moved"]}
    stale = sorted(exempt - needs_one)
    assert not stale, (
        "these declarations state why an insert-time clock is right, and their "
        f"writer no longer dates them that way — delete the reason: {stale}"
    )
    windowed = {
        d.table for d in DECLARATIONS
        if d.cadence.max_silence_days is not None and d.clock is not None
    }
    assert exempt <= windowed, (
        "a clock exemption on a store whose clock is never compared is decoration: "
        f"{sorted(exempt - windowed)}"
    )


@pytest.mark.tripwire
def test_no_windowed_store_has_a_writer_THIS_SCAN_CANNOT_FIND() -> None:
    """FAIL-OPEN CONTROL (DEBT-299). A store whose writer the scan cannot find would
    fall out of the classification entirely and be exempted BY SILENCE — the accident
    that let a model-authored tool escape a logging rule one week ago.

    MEASURED 2026-09-10: the set is EMPTY. The two windowed stores with no literal
    `INSERT INTO <table>` in `src/` — `side_effect_ledger` and `learning_artifacts` —
    both write through an `OwnedRepository` subclass declaring `_table`, and that
    base class builds `INSERT INTO {table} (...) VALUES (...)` with no `ON CONFLICT`
    anywhere in the module, so they are insert-only by construction."""
    unknown = sorted(t for t, _c, _s in _audit()["unknown"])
    assert not unknown, (
        "these stores have a silence window and no writer this scan can classify, "
        "so the rule above cannot see them at all — find the writer and either "
        f"teach the scan its shape or fix the declaration: {unknown}"
    )


@pytest.mark.tripwire
def test_the_scan_sees_a_real_population() -> None:
    """VACUITY CONTROL. A scan that stopped matching `INSERT INTO` would classify
    everything as insert-only and report a clean tree forever."""
    res = _audit()
    assert len(res["moved"]) >= 5, f"only {len(res['moved'])} clocks found in a SET"
    assert len(res["insert_only"]) >= 15, (
        f"only {len(res['insert_only'])} insert-only writers found"
    )
    total = sum(len(v) for v in res.values())
    windowed = sum(
        1 for d in DECLARATIONS
        if d.cadence.max_silence_days is not None and d.clock is not None
    )
    assert total == windowed, (
        f"the scan classified {total} stores and {windowed} have a window — every "
        "windowed store must land in exactly one bucket"
    )


@pytest.mark.tripwire
def test_the_RULE_is_pinned_by_a_SYNTHETIC_tree_not_by_todays_data(
    tmp_path: pathlib.Path,
) -> None:
    """Today's tree cannot tell a working rule from a broken one once the four
    offenders are fixed: every assertion above passes on a scan that always returns
    `insert_only`. So the rule is pinned against a tree built to make the two shapes
    disagree — one store whose upsert omits its clock, one plain INSERT."""
    pkg = tmp_path / "src" / "stackowl"
    pkg.mkdir(parents=True)
    (pkg / "writers.py").write_text(
        'UPSERT = """\n'
        "INSERT INTO synth_upsert (id, created_at, updated_at)\n"
        "VALUES (?, ?, ?)\n"
        "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at\n"
        '"""\n'
        'PLAIN = """\n'
        "INSERT INTO synth_insert (id, created_at) VALUES (?, ?)\n"
        '"""\n',
        encoding="utf-8",
    )
    decls = (
        StoreDeclaration("synth_upsert", Cadence.PERIODIC, "created_at"),
        StoreDeclaration("synth_insert", Cadence.HOT, "created_at"),
        StoreDeclaration("synth_ondemand", Cadence.ON_DEMAND, "created_at"),
        # NO WRITER AT ALL, and it is here because MUTATION TESTING FOUND IT MISSING.
        # Collapsing `unknown` into `insert_only` — "cannot tell" read as "nothing to
        # worry about" — passed every other test in this file, because today's real
        # tree has an EMPTY unknown set and that branch is never taken. It is
        # DEBT-299's accident exactly: a hole that is currently empty is invisible to
        # every assertion about what is IN it.
        StoreDeclaration("synth_no_writer", Cadence.HOT, "created_at"),
    )
    res = audit(tmp_path, decls)
    assert [t for t, _c, _s in res["not_moved"]] == ["synth_upsert"], (
        "a clock outside its own upsert's SET must be reported as NOT MOVED"
    )
    assert [t for t, _c, _s in res["insert_only"]] == ["synth_insert"], (
        "a plain INSERT advances every column it names, so its clock is correct"
    )
    assert [t for t, _c, _s in res["unknown"]] == ["synth_no_writer"], (
        "a store this scan cannot classify must be reported as UNKNOWN, never folded "
        "into insert-only — 'cannot tell' is not an exemption"
    )
    assert not any("synth_ondemand" in t for t, _c, _s in sum(res.values(), [])), (
        "a store with no silence window has no clock to compare and is out of scope"
    )
