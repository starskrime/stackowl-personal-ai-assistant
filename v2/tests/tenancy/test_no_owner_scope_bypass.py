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

# --------------------------------------------------------------------------- #
# Authoritative owner-governed table list.
# Source of truth: src/stackowl/db/migrations/0043_owner_scope_columns.sql
# (the 18 retrofit tables that gained an owner_id column) and
# 0045_durable_tasks.sql (tasks + side_effect_ledger, born owner-scoped).
# The test `test_owner_governed_list_matches_migrations` asserts this set
# equals the set of tables that ADD/define an owner_id column in those files,
# so the constant can never silently drift from the schema.
# --------------------------------------------------------------------------- #
_OWNER_GOVERNED_TABLES: frozenset[str] = frozenset(
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

# A repo root anchor: this file lives at v2/tests/tenancy/, so two parents up.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_SRC_ROOT: Path = _REPO_ROOT / "src" / "stackowl"
_MIGRATIONS_ROOT: Path = _SRC_ROOT / "db" / "migrations"

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
        # --- command-layer helpers (slash commands; not Store subclasses) ---
        # TODO(Epic 9 multi-user): owner-scope cost_records purge in cost_command
        ("commands/cost_command.py", "cost_records"),
        # TODO(Epic 9 multi-user): owner-scope memory-stat reads in memory_command
        ("commands/memory_command.py", "committed_facts"),
        # TODO(Epic 9 multi-user): owner-scope memory-stat reads in memory_helpers
        ("commands/memory_helpers.py", "committed_facts"),
        ("commands/memory_helpers.py", "staged_facts"),
        # TODO(Epic 9 multi-user): owner-scope owl DNA reset in owls_command
        ("commands/owls_command.py", "owl_dna"),
        ("commands/owls_command.py", "dna_checkpoints"),
        # --- import/export bridge (whole-DB transfer; not a Store) ---
        # TODO(Epic 9 multi-user): owner-scope import/export of committed_facts/owl_dna
        ("export/importer.py", "committed_facts"),
        ("export/importer.py", "owl_dna"),
        # --- memory dual-bridge + workers (raw SQL bridges, not Store subclasses) ---
        # TODO(Epic 9 multi-user): owner-scope committed_facts/staged_facts access
        ("memory/budget_enforcer.py", "committed_facts"),
        ("memory/budget_enforcer.py", "staged_facts"),
        ("memory/conversation_miner.py", "committed_facts"),
        ("memory/conversation_miner.py", "staged_facts"),
        ("memory/dream_worker_helpers.py", "committed_facts"),
        ("memory/dream_worker_helpers.py", "staged_facts"),
        ("memory/extraction_handler.py", "conversations"),
        ("memory/extraction_handler.py", "messages"),
        ("memory/fact_promoter.py", "committed_facts"),
        ("memory/fact_promoter.py", "staged_facts"),
        ("memory/fact_reinforcer.py", "staged_facts"),
        ("memory/kuzu_sync_handler.py", "committed_facts"),
        ("memory/pruner.py", "committed_facts"),
        ("memory/pruner.py", "staged_facts"),
        ("memory/sqlite_bridge.py", "committed_facts"),
        ("memory/sqlite_bridge.py", "staged_facts"),
        ("memory/sqlite_helpers.py", "committed_facts"),
        # --- owl evolution engine (raw SQL; not a Store subclass) ---
        # TODO(Epic 9 multi-user): owner-scope owl_dna/messages/conversations in evolution
        ("owls/evolution.py", "owl_dna"),
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
    """The hardcoded table set must equal owner_id tables in 0043 + 0045.

    Guards against the constant drifting from the schema. We parse the two
    migration files for every table that adds/defines an ``owner_id`` column.
    """
    add_col_re = re.compile(
        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+owner_id\b", re.IGNORECASE
    )
    # tasks/side_effect_ledger define owner_id inline in CREATE TABLE.
    create_re = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE)

    comment_re = re.compile(r"--[^\n]*")

    discovered: set[str] = set()
    for name in ("0043_owner_scope_columns.sql", "0045_durable_tasks.sql"):
        raw = (_MIGRATIONS_ROOT / name).read_text(encoding="utf-8")
        # Strip SQL line comments so prose like "owner_id is enforced" or a
        # "CREATE TABLE ..." mention inside a comment cannot be misparsed.
        sql = comment_re.sub("", raw)
        discovered.update(m.group(1) for m in add_col_re.finditer(sql))
        # For CREATE TABLE migrations, only count tables whose body has owner_id.
        for m in create_re.finditer(sql):
            table = m.group(1)
            # crude body slice: from this CREATE to the next ';'
            body = sql[m.end() : sql.find(";", m.end())]
            if "owner_id" in body.lower():
                discovered.add(table)

    assert discovered == set(_OWNER_GOVERNED_TABLES), (
        "owner-governed table list drifted from migrations 0043/0045. "
        f"In migrations not in constant: {discovered - set(_OWNER_GOVERNED_TABLES)}; "
        f"in constant not in migrations: {set(_OWNER_GOVERNED_TABLES) - discovered}"
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
     WHERE c.session_id = ?
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
