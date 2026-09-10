"""CI guard: no SQL on an owner-governed table may bypass owner-scoping.

Story 1.1 Pass 4 — owner-scope regression lint.

Pass 1 added an ``owner_id`` column (and, for shareable entities, a
``visibility`` column) to every user-DATA table (migration ``0043``), and the
durable-task substrate was born owner-scoped (migration ``0045``). Pass 2
refactored 8 domain ``Store`` classes onto :class:`OwnedRepository` so their
SQL is structurally scoped by ``owner_id``.

This module is the **regression fence**: it scans ``src/stackowl`` for SQL
statements that operate on an owner-governed table WITHOUT mentioning
``owner_id``. Such a statement can read or write another principal's rows, so
it is a tenancy violation. New code that forgets ``owner_id`` fails this test.

Two things keep the guard honest:

* :data:`_OWNER_GOVERNED_TABLES` is the authoritative list of tables that carry
  an ``owner_id`` column. Its source of truth is migrations ``0043`` (the 18
  retrofit tables) and ``0045`` (``tasks`` + ``side_effect_ledger``). The test
  cross-checks this constant against the migrations so the two cannot drift.

* :data:`_KNOWN_UNSCOPED_ALLOWLIST` enumerates the pre-existing accessors that
  legitimately do NOT yet scope by owner (memory dual-bridge, command-layer
  helpers, knowledge tools, etc. — none of which were refactored in Pass 2
  because they are not ``Store`` subclasses). Each entry is a tracked gap with
  a ``TODO(Epic 9 multi-user)`` rationale — NOT a silent pass. Any violation
  outside the allowlist fails the build, so NEW unscoped code is blocked while
  existing known-gaps remain visible and accountable.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

# CROSS-CUTTING GUARD. This protects a property of the WHOLE repo, so a
# per-item test run never selects it — which is how two real bypasses
# shipped (an unscoped task_outcomes read, and three stale allowlist
# entries for deleted modules). `scripts/tripwires.sh` runs everything
# marked this way, whatever the change touched.
pytestmark = pytest.mark.tripwire

# --------------------------------------------------------------------------- #
# Authoritative owner-governed table list — DERIVED, since 2026-09-10 (DEBT-291).
#
# It used to be this hand-written set, pinned by a drift test to TWO migrations:
# `0043_owner_scope_columns.sql` and `0045_durable_tasks.sql`. The comment said
# the constant "can never silently drift from the schema", and the test it named
# did compare the two exactly — against a schema frozen at migration 0045.
#
# MEASURED 2026-09-10 against the live database: **30 tables carry an `owner_id`
# column and this set named 17 of them.** Every table that gained one in a LATER
# migration was invisible to the guard AND to the test that existed to stop
# exactly this: `approach_rating_pending` (0084), `objectives`,
# `objective_subgoals`, `objective_events`, `learning_artifacts`,
# `message_ledger`, `owl_dna_authored`, `owls`, `sessions`, `skill_ownership`,
# `undelivered_outbox`, `command_sequence_edges`, `command_sequence_last`.
# Three REAL unscoped accessors were hiding behind that gap.
#
# So the set is now read from EVERY migration rather than from two remembered
# ones. `_HISTORICAL_FLOOR` below is the vacuity control: the derivation must
# still find the original twenty, so a parser that silently stops matching
# cannot quietly disarm the whole guard.
# --------------------------------------------------------------------------- #
_HISTORICAL_FLOOR: frozenset[str] = frozenset(
    {
        # --- migration 0043 (18 retrofit tables) ---
        "conversations",
        "messages",
        "memory_facts",
        "staged_facts",
        "committed_facts",
        "fact_rejections",
        "owl_profiles",
        "owl_dna",
        "dna_checkpoints",
        "pellets",
        "parliament_sessions",
        "cost_records",
        "task_outcomes",
        "reflections",
        "tool_heuristics",
        "user_preferences",
        "onboarding",
        "skills",
        # --- migration 0045 (durable task substrate, born owner-scoped) ---
        "tasks",
        "side_effect_ledger",
    }
)

_OWNER_ID_ADD_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+owner_id\b", re.IGNORECASE
)
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE
)
_SQL_COMMENT_RE = re.compile(r"--[^\n]*")


def _discover_owner_governed_tables() -> frozenset[str]:
    """Every table any migration gives an ``owner_id`` column.

    Reads the WHOLE migrations directory. SQL line comments are stripped first,
    so prose like "owner_id is enforced" or a `CREATE TABLE` named inside a
    comment cannot be misparsed — the same precaution the two-file version took,
    kept because it was right.
    """
    discovered: set[str] = set()
    for path in sorted(_MIGRATIONS_ROOT.glob("*.sql")):
        sql = _SQL_COMMENT_RE.sub("", path.read_text(encoding="utf-8"))
        discovered.update(m.group(1) for m in _OWNER_ID_ADD_RE.finditer(sql))
        for m in _CREATE_TABLE_RE.finditer(sql):
            end = sql.find(";", m.end())
            body = sql[m.end() : end if end != -1 else len(sql)]
            if "owner_id" in body.lower():
                discovered.add(m.group(1))
    return frozenset(discovered)




# A repo root anchor: this file lives at v2/tests/tenancy/, so two parents up.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_SRC_ROOT: Path = _REPO_ROOT / "src" / "stackowl"
_MIGRATIONS_ROOT: Path = _SRC_ROOT / "db" / "migrations"

# Derived HERE rather than beside its function, because the paths it reads are
# defined on the line above — module-level order is the whole constraint.
_OWNER_GOVERNED_TABLES: frozenset[str] = _discover_owner_governed_tables()

# A string literal is treated as a SQL statement only if it contains a real
# DML verb. This filters out prose/docstrings that merely happen to contain a
# table-like word.
_DML_VERB_RE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|SELECT|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\b",
    re.IGNORECASE,
)

# The owner-scoping predicate/column we require to be present.
_OWNER_TOKEN = "owner_id"


def _table_relation_re(table: str) -> re.Pattern[str]:
    """Match ``table`` used as a SQL relation: FROM/JOIN/INTO/UPDATE <table>.

    Requiring a relational keyword in front of the name (rather than a bare
    word match) avoids flagging a table name that appears only inside prose or
    a column alias.
    """
    return re.compile(
        r"\b(?:FROM|JOIN|INTO|UPDATE)\s+" + re.escape(table) + r"\b",
        re.IGNORECASE,
    )


# Pre-compile one relation matcher per governed table.
_RELATION_RES: dict[str, re.Pattern[str]] = {
    t: _table_relation_re(t) for t in _OWNER_GOVERNED_TABLES
}


@dataclass(frozen=True, slots=True)
class Violation:
    """A single SQL statement on an owner-governed table lacking owner_id."""

    table: str
    snippet: str

    def signature(self, file_rel: str) -> tuple[str, str]:
        """Stable (file, table) key used to match against the allowlist."""
        return (file_rel, self.table)


class OwnerScopeDetector:
    """Pure, unit-testable detector for owner-scope bypasses in SQL strings.

    The detector is deliberately self-contained (no filesystem, no DB): callers
    feed it a source string, it returns the violations found in that string's
    SQL literals. This lets the self-check exercise the exact logic the repo
    scan relies on.
    """

    def __init__(self, governed_tables: frozenset[str] = _OWNER_GOVERNED_TABLES) -> None:
        self._tables = governed_tables
        self._relations = {t: _table_relation_re(t) for t in governed_tables}

    def is_sql_statement(self, literal: str) -> bool:
        """True if the string literal looks like a DML SQL statement."""
        return bool(_DML_VERB_RE.search(literal))

    def violations_in_statement(self, sql: str) -> list[str]:
        """Return the governed tables this single SQL statement bypasses.

        A table is a violation when the statement references it as a relation
        (FROM/JOIN/INTO/UPDATE <table>) but the statement does not mention
        ``owner_id`` anywhere.
        """
        if not self.is_sql_statement(sql):
            return []
        if _OWNER_TOKEN in sql.lower():
            return []
        hits: list[str] = []
        for table, rel in self._relations.items():
            if rel.search(sql):
                hits.append(table)
        return sorted(hits)

    def scan_source(self, source: str) -> list[Violation]:
        """Scan a Python source string for owner-scope bypasses.

        Extracts every ``str`` constant via AST, treats SQL-looking ones as
        statements, and records a :class:`Violation` per (statement, table)
        bypass. Falls back to no results on unparseable source.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        violations: list[Violation] = []
        seen: set[tuple[str, str]] = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            literal = node.value
            for table in self.violations_in_statement(literal):
                key = (table, literal)
                if key in seen:
                    continue
                seen.add(key)
                snippet = " ".join(literal.split())[:100]
                violations.append(Violation(table=table, snippet=snippet))
        return violations


# --------------------------------------------------------------------------- #
# KNOWN UNSCOPED ALLOWLIST — tracked gaps, NOT silent passes.
#
# Each entry is a (src-relative path, table) pair for a pre-existing accessor
# that issues raw SQL on an owner-governed table WITHOUT an owner_id predicate
# and was NOT refactored in Pass 2 (these are not OwnedRepository Store
# subclasses — they are memory dual-bridge helpers, command-layer helpers,
# knowledge/scheduling tools, the evolution engine, and the import/export
# bridge). They are single-user-safe today (one default principal) but MUST be
# owner-scoped before multi-user ships.
#
# TODO(Epic 9 multi-user): owner-scope every accessor listed below. This list
# is the canonical gap register for that work — do not grow it for NEW code;
# new unscoped SQL must be fixed, not allowlisted.
# --------------------------------------------------------------------------- #
_KNOWN_UNSCOPED_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # --- became VISIBLE 2026-09-10 when the governed set stopped being two
        #     remembered migrations (DEBT-291). Pre-existing, not new.
        # TODO(Epic 9 multi-user): `SessionStore` is not an `OwnedRepository` — it
        # has no `_owner_id` at all, so scoping it means threading an owner through
        # the constructor and every caller, which is the Pass-2 refactor this
        # allowlist was created to defer. MEASURED 2026-09-10, and it is why this
        # is a queued decision rather than an open hole: ONE principal, 143
        # sessions, ONE distinct owner_id — no cross-owner exposure exists today.
        # Five of its seven statements key on `session_key`, which is the table's
        # PRIMARY KEY, so they cannot reach another owner's row even unscoped; the
        # two that can are the enumerations (`ORDER BY updated_at DESC` and the
        # retention sweep). See ESC-166.
        ("sessions/store.py", "sessions"),
        # --- command-layer helpers (slash commands; not Store subclasses) ---
        # TODO(Epic 9 multi-user): owner-scope cost_records purge in cost_command
        ("commands/cost_command.py", "cost_records"),
        # TODO(Epic 9 multi-user): owner-scope memory-stat reads in memory_command
        # TODO(Epic 9 multi-user): owner-scope memory-stat reads in memory_helpers
        ("commands/memory_helpers.py", "committed_facts"),
        ("commands/memory_helpers.py", "staged_facts"),
        # TODO(Epic 9 multi-user): owner-scope owl DNA reset in owls_command
        ("commands/owls_command.py", "owl_dna"),
        # ("commands/owls_command.py", "dna_checkpoints") REMOVED 2026-09-01 —
        # the cascade moved into OwlStore.delete on 2026-08-31, so this entry
        # went stale and test_allowlist_has_no_stale_entries said so. The moved
        # code is now owner-scoped rather than re-allowlisted under its new path:
        # moving a file must not be a way to acquire an exemption.
        # --- import/export bridge (whole-DB transfer; not a Store) ---
        # TODO(Epic 9 multi-user): owner-scope import/export of committed_facts/owl_dna
        ("export/importer.py", "committed_facts"),
        ("export/importer.py", "owl_dna"),
        # --- DELIBERATELY owner-agnostic (NOT an Epic 9 TODO) ---
        # pipeline/durable/store.py's any_active_task_for_lane is a module
        # function precisely BECAUSE it must not be owner-scoped, and its
        # docstring says so at length: the caller is the background sweeper,
        # which has no principal, and tasks are created under whichever owner
        # happened to be in scope while a lane's identity is the PERSON. An
        # owner-scoped read would match nothing and invariant I4 would become a
        # silent no-op — the exact failure D01.7 kept finding. The LANE is the
        # scope here. Listed so the detector stays honest; do NOT "fix" this by
        # adding owner_id, and do not retitle it as deferred debt.
        ("pipeline/durable/store.py", "tasks"),
        # skills/store.py's prune_fts is owner-agnostic for the SAME kind of
        # reason, and scoping it would introduce a cross-tenant DELETE rather
        # than prevent one. It removes skills_fts rows whose name is absent from
        # `skills`; skills_fts has NO owner column and mirrors the table
        # globally, so an owner-scoped subquery would prune ANOTHER principal's
        # live index entries. Its emptiness guard ("is `skills` empty at all?")
        # is global for the same reason: if any principal has skills, the index
        # is not wholesale-stale. Added 2026-09-01 after prune_fts (3f82d5d5)
        # tripped the detector; listed so the detector stays honest. Do NOT
        # "fix" this by adding owner_id — that is the bug, not the fix.
        ("skills/store.py", "skills"),
        # --- memory dual-bridge + workers (raw SQL bridges, not Store subclasses) ---
        # TODO(Epic 9 multi-user): owner-scope committed_facts/staged_facts access
        # memory/extraction_handler.py entries removed 2026-07-26: the file was
        # DELETED by D01.7 slice 3b part 5b (c2fc9d32) — it was registered at
        # boot and never enqueued by anything, so the rollover boundary took
        # over its job. An allowlist entry for a file that does not exist is
        # exactly the rot test_allowlist_has_no_stale_entries guards against.
        # Six entries removed 2026-08-14 by D08.2 seam 3, and the removals span
        # FOUR passes rather than one — which is why this guard earns its keep:
        # memory/pruner.py (pass 2) and memory/fact_promoter.py (pass 4) are DELETED
        # files, while commands/memory_command.py (seam 3 part 1) and
        # memory/dream_worker_helpers.py (pass 3) still exist but no longer carry the
        # unscoped SQL. Two of the six had been stale since earlier passes and nobody
        # noticed until this test was run — an allowlist entry for a closed gap is the
        # rot test_allowlist_has_no_stale_entries exists to catch.
        #
        # The seventh — memory/dream_worker_helpers.py :: committed_facts — was left
        # behind by MY OWN deletion of that module hours after clearing the other
        # six, and the guard caught it the same day. Removing a file and leaving its
        # register entry is evidently the easy half to forget; this test is why it
        # cost minutes instead of months.
        ("memory/sqlite_bridge.py", "committed_facts"),
        ("memory/sqlite_bridge.py", "staged_facts"),
        ("memory/sqlite_helpers.py", "committed_facts"),
        # --- owl evolution engine (raw SQL; not a Store subclass) ---
        # TODO(Epic 9 multi-user): owner-scope messages/conversations in evolution
        # NOTE: owl_dna removed — evolution.py delegates DNA persistence to
        # dna_storage.upsert_owl_dna (no owl_dna SQL literal in evolution.py).
        ("owls/evolution.py", "messages"),
        ("owls/evolution.py", "conversations"),
        # --- knowledge/scheduling tools (agent-callable; not Store subclasses) ---
        # TODO(Epic 9 multi-user): owner-scope conversation/message reads in knowledge tools
        ("tools/knowledge/session_access.py", "conversations"),
        ("tools/knowledge/session_search.py", "conversations"),
        ("tools/knowledge/session_search.py", "messages"),
        ("tools/knowledge/transcripts.py", "conversations"),
        ("tools/knowledge/transcripts.py", "messages"),
        ("tools/scheduling/cron_helpers.py", "conversations"),
        # --- TUI onboarding-banner upsert (not a Store subclass) ---
        # TODO(Epic 9 multi-user): owner-scope onboarding banner state in parliament_panel
        ("tui/widgets/parliament_panel_helpers.py", "onboarding"),
    }
)


def _iter_source_files() -> list[Path]:
    """All scannable ``src/stackowl`` Python files, excluding migrations.

    Migrations DEFINE schema (CREATE/ALTER/INDEX) rather than owner-scoped DML,
    so the whole migrations directory is exempt.
    """
    files: list[Path] = []
    for py in sorted(_SRC_ROOT.rglob("*.py")):
        if _MIGRATIONS_ROOT in py.parents:
            continue
        files.append(py)
    return files


def _scan_repo() -> list[tuple[str, Violation]]:
    """Scan the whole source tree; return (src-relative path, Violation) pairs."""
    detector = OwnerScopeDetector()
    found: list[tuple[str, Violation]] = []
    for py in _iter_source_files():
        rel = py.relative_to(_SRC_ROOT).as_posix()
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for violation in detector.scan_source(text):
            found.append((rel, violation))
    return found


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_owner_governed_list_matches_migrations() -> None:
    """The derived set must still contain every table the original two named.

    THIS TEST USED TO BE THE DEFECT. It asserted the hand-written set EQUALLED
    the owner_id tables in migrations 0043 and 0045 — two files, frozen — and
    passed for months while the schema grew thirteen more owner-governed tables
    the guard could not see. A drift test anchored to a point in history cannot
    detect drift; it only pins the past.

    Now the set is derived from every migration, and this is the VACUITY CONTROL:
    the derivation must still find the original twenty. A parser that stops
    matching would otherwise empty the set and disarm the whole guard silently,
    which is the one failure mode a derived set has that a literal does not.
    """
    missing = _HISTORICAL_FLOOR - _OWNER_GOVERNED_TABLES
    assert not missing, (
        "the derivation stopped finding tables migrations 0043/0045 grant an "
        f"owner_id: {sorted(missing)}. The guard is now weaker than the "
        "hand-written set it replaced."
    )
    assert len(_OWNER_GOVERNED_TABLES) > len(_HISTORICAL_FLOOR), (
        "the derived set is no larger than the original twenty, which was true "
        "at migration 0045 and has not been true since 0084. Either the parser "
        "regressed or every later migration stopped granting owner_id."
    )


def test_detector_flags_unscoped_statement() -> None:
    """DETECTOR self-check: an unscoped INSERT on a governed table is flagged."""
    detector = OwnerScopeDetector()
    bad = 'INSERT INTO messages (id, role, content) VALUES (?, ?, ?)'
    assert detector.violations_in_statement(bad) == ["messages"]


def test_detector_passes_scoped_statement() -> None:
    """DETECTOR self-check: the same statement WITH owner_id is clean."""
    detector = OwnerScopeDetector()
    good = (
        "INSERT INTO messages (id, owner_id, role, content) "
        "VALUES (?, ?, ?, ?)"
    )
    assert detector.violations_in_statement(good) == []


def test_detector_ignores_prose_and_non_governed_tables() -> None:
    """DETECTOR self-check: prose and unrelated tables are not flagged."""
    detector = OwnerScopeDetector()
    # Prose mentioning a table word but no SQL verb.
    assert detector.violations_in_statement("handle inbound messages here") == []
    # A governed-table word that is not used as a relation.
    assert detector.violations_in_statement("SELECT count(*) FROM job_runs") == []
    # FTS shadow table is not owner-governed.
    assert (
        detector.violations_in_statement("DELETE FROM committed_facts_fts WHERE rowid = ?")
        == []
    )


def test_detector_scans_multiline_and_constant_sql() -> None:
    """DETECTOR self-check: triple-quoted / constant-assigned SQL is scanned."""
    detector = OwnerScopeDetector()
    source = '''
_SELECT = """
    SELECT m.role, m.content
      FROM messages m
      JOIN conversations c ON c.id = m.conversation_id
     WHERE c.session_key = ?
"""
_SCOPED = "DELETE FROM pellets WHERE owner_id = ? AND id = ?"
'''
    tables = {v.table for _src in [source] for v in detector.scan_source(_src)}
    # messages + conversations flagged; pellets is scoped so excluded.
    assert tables == {"messages", "conversations"}


def test_repo_has_no_unscoped_sql_outside_allowlist() -> None:
    """REPO scan: every owner-scope bypass must be in the documented allowlist.

    A new unscoped query (not in :data:`_KNOWN_UNSCOPED_ALLOWLIST`) fails here.
    """
    found = _scan_repo()
    offending: set[tuple[str, str]] = set()
    for rel, violation in found:
        sig = violation.signature(rel)
        if sig not in _KNOWN_UNSCOPED_ALLOWLIST:
            offending.add(sig)

    assert not offending, (
        "New owner-scope bypass(es) detected. Each SQL statement on an "
        "owner-governed table must include an owner_id predicate. If this is a "
        "genuinely pre-existing gap, scope it by owner_id rather than "
        "allowlisting new code:\n  "
        + "\n  ".join(f"{f} :: {t}" for f, t in sorted(offending))
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every allowlist entry must still correspond to a real current violation.

    Once an accessor is owner-scoped, its allowlist entry should be removed.
    A stale entry means the gap was closed but the register not updated.
    """
    found = _scan_repo()
    live = {v.signature(rel) for rel, v in found}
    stale = _KNOWN_UNSCOPED_ALLOWLIST - live
    assert not stale, (
        "Stale _KNOWN_UNSCOPED_ALLOWLIST entries (no longer a violation — "
        "remove them now that the gap is closed):\n  "
        + "\n  ".join(f"{f} :: {t}" for f, t in sorted(stale))
    )
