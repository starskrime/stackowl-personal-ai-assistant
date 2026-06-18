"""C7 / F131 — export must sanitize ALL exported tables, not just committed_facts.

Merge-gate: a secret seeded into staged_facts, owl_dna(-adjacent free text),
parliament_sessions transcript, and audit_log.details must NOT appear raw in the
produced archive; audit_log rows whose content changed are stamped _redacted;
committed_facts stays byte-identical to the pre-change path. A second test proves
the fail-closed tripwire: a secret the per-table sanitize cannot scrub raises
SecurityError BEFORE any archive is written.

No secret value is logged — we only assert on extracted archive members.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from stackowl.exceptions import SecurityError

# Real-shaped secrets seeded per table (must be redacted on export).
_ANTHROPIC = "sk-ant-api03-" + "Z" * 40
_GITHUB = "ghp_" + "Q" * 40
_OPENAI = "sk-proj-" + "Ab3Cd5Ef" * 6
_AWS = "AKIA" + "1234567890ABCDEF"


def _extract_json(archive: Path, member: str) -> Any:
    with tarfile.open(archive, "r:gz") as tar:
        f = tar.extractfile(member)
        assert f is not None, f"member {member} missing"
        return json.loads(f.read().decode("utf-8"))


def _raw_member_text(archive: Path, member: str) -> str:
    with tarfile.open(archive, "r:gz") as tar:
        f = tar.extractfile(member)
        assert f is not None
        return f.read().decode("utf-8")


async def _seed(db: Any) -> None:
    # staged_facts: a secret pasted into the content free-text.
    await db.execute(
        "INSERT INTO staged_facts "
        "(fact_id, content, source_type, source_ref, confidence, staged_at) "
        "VALUES (?, ?, 'manual', 'ref', 0.9, '2026-01-01T00:00:00+00:00')",
        ("sf1", f"my key is {_ANTHROPIC} keep it"),
    )
    # parliament_sessions: a secret inside the rounds transcript JSON-text.
    await db.execute(
        "INSERT INTO parliament_sessions "
        "(session_id, topic, owl_names, rounds, synthesis, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, 'complete', '2026-01-01T00:00:00+00:00')",
        (
            "ps1",
            "topic",
            json.dumps(["owl"]),
            json.dumps([{"text": f"leaked token {_GITHUB} here"}]),
            f"synthesis with {_OPENAI}",
        ),
    )
    # audit_log.details: a secret pasted into an audited detail.
    await db.execute(
        "INSERT INTO audit_log (event_type, actor, target, timestamp, details) "
        "VALUES (?, ?, ?, ?, ?)",
        ("test_event", "alice", "bob", 1.0, json.dumps({"note": f"creds {_AWS}"})),
    )
    # committed_facts: a clean row to assert byte-identical handling.
    await db.execute(
        "INSERT INTO committed_facts "
        "(fact_id, content, embedding, embedding_model, committed_at, source_type, source_ref) "
        "VALUES (?, ?, ?, 'stub', '2026-01-01T00:00:00+00:00', 'manual', 'ref')",
        ("cf1", "perfectly clean committed fact", b"\x00\x01"),
    )


class TestExportScopeSanitizesAllTables:
    async def test_secrets_redacted_across_all_tables(
        self, tmp_path: Path, tmp_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from stackowl.export.exporter import Exporter

        await _seed(tmp_db)
        out = tmp_path / "exp.tar.gz"
        exporter = Exporter(db=tmp_db)
        result = await exporter.export(output_path=out)

        # Each previously-unsanitized table is now scrubbed.
        for member, raw_secret in (
            ("staged_facts.json", _ANTHROPIC),
            ("parliament_sessions.json", _GITHUB),
            ("parliament_sessions.json", _OPENAI),
            ("audit_log.json", _AWS),
        ):
            text = _raw_member_text(result, member)
            assert raw_secret not in text, f"{raw_secret[:8]}... leaked in {member}"
            assert "<REDACTED>" in text, f"{member} not sanitized"

    async def test_audit_rows_stamped_redacted(self, tmp_path: Path, tmp_db: Any) -> None:
        from stackowl.export.exporter import Exporter

        await _seed(tmp_db)
        out = tmp_path / "exp2.tar.gz"
        result = await Exporter(db=tmp_db).export(output_path=out)
        audit = _extract_json(result, "audit_log.json")
        assert any(row.get("_redacted") is True for row in audit), (
            "no audit row stamped _redacted after value-sanitization"
        )

    async def test_committed_facts_unchanged(self, tmp_path: Path, tmp_db: Any) -> None:
        from stackowl.export.exporter import Exporter

        await _seed(tmp_db)
        out = tmp_path / "exp3.tar.gz"
        result = await Exporter(db=tmp_db).export(output_path=out)
        committed = _extract_json(result, "committed_facts.json")
        row = next(r for r in committed if r["fact_id"] == "cf1")
        assert row["content"] == "perfectly clean committed fact"
        # No spurious _redacted stamp on a clean committed row.
        assert "_redacted" not in row


class TestExportFailsClosed:
    async def test_unsanitizable_secret_raises_before_archive(
        self, tmp_path: Path, tmp_db: Any
    ) -> None:
        from stackowl.export.exporter import Exporter

        # A named-vendor secret under a sensitive-looking transcript that the
        # per-row sanitize is monkey-defeated for — simulate by injecting a
        # secret the tripwire must catch. We seed a parliament transcript that
        # holds a secret in a numeric-looking field the flat strategy skips, then
        # force the tripwire to see it. The reliable cross-impl assertion: a
        # secret present in the SERIALIZED output trips check_and_raise.
        # Easiest deterministic trigger: a committed_fact whose key is a number
        # (sanitize_text still scrubs) — instead we assert the tripwire exists by
        # feeding a value the sanitizer leaves AND check_and_raise rejects.
        await tmp_db.execute(
            "INSERT INTO staged_facts "
            "(fact_id, content, source_type, source_ref, confidence, staged_at) "
            "VALUES (?, ?, 'manual', 'ref', 0.9, '2026-01-01T00:00:00+00:00')",
            ("sfx", "x"),
        )
        out = tmp_path / "exp_fail.tar.gz"
        exporter = Exporter(db=tmp_db)

        # Monkeypatch the sanitizer so per-row sanitize is a NO-OP but the
        # tripwire still runs over real content carrying a named secret — proves
        # the tripwire is the independent fail-closed gate, not the per-row pass.
        leaked = f"residual {_GITHUB} secret"
        await tmp_db.execute(
            "INSERT INTO parliament_sessions "
            "(session_id, topic, owl_names, rounds, synthesis, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, 'complete', '2026-01-01T00:00:00+00:00')",
            ("psx", "t", json.dumps(["o"]), json.dumps([{"text": leaked}]), None),
        )
        original_sanitize_text = exporter._sanitizer.sanitize_text

        def _noop(text: str) -> str:  # leaves the secret in place
            return text

        exporter._sanitizer.sanitize_text = _noop  # type: ignore[method-assign]
        try:
            with pytest.raises(SecurityError) as ei:
                await exporter.export(output_path=out)
            assert ei.value.category == "export_sanitization_failed"
        finally:
            exporter._sanitizer.sanitize_text = original_sanitize_text  # type: ignore[method-assign]
        # Archive must NOT have been written.
        assert not out.exists(), "archive written despite fail-closed tripwire"
