"""One migrated-schema template per process, copied instead of re-migrated (DEBT-38).

MEASURED 2026-08-30: ``MigrationRunner().run()`` takes 9.25s on this box, because
there are 128 migrations and each commits in its own exclusive transaction — 128
fsyncs at ~72ms. 245 test files reach that through the ``tmp_db`` fixture (fixed
in conftest) and 144 more construct the runner directly. This is for the direct
callers, which cannot take a session-scoped fixture without editing every
signature.

That per-migration durability is CORRECT for a real boot — a crash mid-migration
must leave a consistent ledger, and the runner argues for it in its own comments.
It is worthless for a temp file deleted at the end of a test, so this is a
test-side change only and production semantics are untouched.

A COPY IS NOT AN APPROXIMATION: 226 schema objects and all 128 ledger rows are
identical between a copied database and a freshly migrated one. Verified before
adopting.

DO NOT USE THIS IN A TEST *OF* THE MIGRATIONS. ``tests/db/`` and the
``test_migration_*`` files must run the real runner — they are asserting on what
it does, and a template would make them assert on a cached artefact of
themselves. Those call sites are deliberately left alone.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner

_TEMPLATE: Path | None = None


def _template() -> Path:
    """Build the migrated schema once per process, then reuse it."""
    global _TEMPLATE  # noqa: PLW0603 — one template per process, deliberately
    if _TEMPLATE is None or not _TEMPLATE.exists():
        path = Path(tempfile.mkdtemp(prefix="stackowl-schema-")) / "template.db"
        MigrationRunner(db_path=path).run()
        _TEMPLATE = path
    return _TEMPLATE


def seed_schema(db_path: Path | str) -> Path:
    """Give ``db_path`` the full migrated schema. Drop-in for ``MigrationRunner(...).run()``.

    Returns the path, so a caller can write ``db = seed_schema(tmp_path / "t.db")``.
    """
    dest = Path(db_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_template(), dest)
    return dest
