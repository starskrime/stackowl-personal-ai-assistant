"""Tests for the trust field on StagedFact and MemoryRecord (Story E)."""

from datetime import UTC, datetime

from stackowl.memory.models import MemoryRecord, StagedFact


def test_stagedfact_trust_defaults_untrusted():
    f = StagedFact(content="x", source_type="conversation", source_ref="s", confidence=0.5)
    assert f.trust == "untrusted"


def test_stagedfact_accepts_explicit_trust():
    f = StagedFact(content="x", source_type="manual", source_ref="s", confidence=1.0, trust="trusted")
    assert f.trust == "trusted"


def test_memoryrecord_trust_field_and_default():
    r = MemoryRecord(
        fact_id="f",
        content="c",
        embedding=[0.1],
        embedding_model="m",
        committed_at=datetime.now(UTC),
        source_type="webpage",
        source_ref="s",
        trust="untrusted",
    )
    assert r.trust == "untrusted"

    r2 = MemoryRecord(
        fact_id="f",
        content="c",
        embedding=[0.1],
        embedding_model="m",
        committed_at=datetime.now(UTC),
        source_type="webpage",
        source_ref="s",
    )
    assert r2.trust == "untrusted"  # default
