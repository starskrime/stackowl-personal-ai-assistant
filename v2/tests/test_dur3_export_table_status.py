"""DUR-3 / F135 — export reports a per-table status and never masks a real
query failure as an empty table.

* A genuinely missing table (legitimate-empty) exports as ``[]`` AND records a
  truthful per-table status in ``export-manifest.json`` (``missing`` / ``ok``).
* A transient / real DB failure (anything other than no-such-table) is NOT
  swallowed to ``[]`` — it re-raises so the archive is never silently
  half-written, and is recorded as ``failed`` in the manifest before surfacing.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from stackowl.export.exporter import Exporter


def _extract_json(archive: Path, member: str) -> Any:
    with tarfile.open(archive, "r:gz") as tar:
        f = tar.extractfile(member)
        assert f is not None, f"member {member} missing"
        return json.loads(f.read().decode("utf-8"))


async def test_manifest_records_per_table_status_ok(tmp_path: Path, tmp_db: Any) -> None:
    """All tables present → manifest carries a table_status map with ok/missing."""
    out = tmp_path / "exp.tar.gz"
    result = await Exporter(db=tmp_db).export(output_path=out)
    manifest = _extract_json(result, "export-manifest.json")
    assert "table_status" in manifest
    status = manifest["table_status"]
    # Every declared exported table appears with a non-failed status.
    assert set(status) >= {
        "committed_facts",
        "staged_facts",
        "owl_dna",
        "parliament_sessions",
        "audit_log",
    }
    assert all(v in ("ok", "missing", "empty") for v in status.values())


async def test_missing_table_is_legitimate_empty(tmp_path: Path, tmp_db: Any) -> None:
    """A dropped table exports as [] and is recorded as 'missing', not 'failed'."""
    await tmp_db.execute("DROP TABLE owl_dna")
    out = tmp_path / "exp_missing.tar.gz"
    result = await Exporter(db=tmp_db).export(output_path=out)
    rows = _extract_json(result, "owl_dna.json")
    assert rows == []
    manifest = _extract_json(result, "export-manifest.json")
    assert manifest["table_status"]["owl_dna"] == "missing"


async def test_real_query_failure_is_not_masked_as_empty(
    tmp_path: Path, tmp_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-missing-table DB error re-raises rather than exporting empty."""

    async def _boom(sql: str, params: Any = ()) -> list[dict]:  # type: ignore[type-arg]
        raise RuntimeError("transient backend explosion")

    monkeypatch.setattr(tmp_db, "fetch_all", _boom)
    out = tmp_path / "exp_fail.tar.gz"
    with pytest.raises(RuntimeError, match="transient backend explosion"):
        await Exporter(db=tmp_db).export(output_path=out)
    # No half-written archive on a genuine failure.
    assert not out.exists()
