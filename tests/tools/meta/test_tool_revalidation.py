"""Back-catalog re-validation of learned tools (Branch 4b).

A learned tool that, in its whole outcome history, NEVER produced a trustworthy
success (success=1 AND failure_class IS NULL) but has been tried enough times to
judge — the ``instagram_media_extractor`` class (claims success, produces nothing)
— is quarantined out of the live catalog so the boot loader stops re-registering it.

These are real-DB + real-FS integration tests over the revalidation function the CLI
wraps. Confirms the function keys on the TRUSTWORTHY signal (the B4b failure_class),
not raw success, and that a tool WITH a clean win, or one with too-thin evidence, is
kept.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.tools.meta.tool_revalidation import revalidate_learned_tools


def _write_spec(directory: Path, name: str) -> Path:
    spec = {
        "spec_version": 1,
        "name": name,
        "description": f"{name} does a thing",
        "params": [],
        "argv_template": ["echo", "hi"],
        "action_severity": "write",
    }
    path = directory / f"{name}.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


async def _record(store: TaskOutcomeStore, trace: str, tool: str, *, success: bool,
                  failure_class: str | None) -> None:
    await store.record(
        trace_id=trace, session_id="s", owl_name="secretary", channel="cli",
        success=success, latency_ms=1.0, tool_call_count=1, failure_class=failure_class,
        step_durations={}, input_text="x", response_text="y", tool_sequence=(tool,),
    )


@pytest.mark.asyncio
async def test_false_win_only_tool_is_quarantined(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """A learned tool whose every outcome is a non-trustworthy 'success' (the B4b
    failure_class set) is evicted from the live catalog by default."""
    learned = tmp_path / "learned"
    quarantine = tmp_path / "quarantine"
    learned.mkdir()
    spec_path = _write_spec(learned, "fake_extractor")

    store = TaskOutcomeStore(tmp_db)
    # Three false wins — enough evidence, ZERO trustworthy successes.
    for i in range(3):
        await _record(store, f"t{i}", "fake_extractor", success=False,
                      failure_class="unachieved_effect")

    report = await revalidate_learned_tools(
        tmp_db, learned, min_evidence=3, quarantine_dir=quarantine,
    )

    assert "fake_extractor" in report.evicted, (
        "a learned tool that only ever produced false wins must be evicted."
    )
    assert not spec_path.exists(), "the suspect spec was not removed from the live dir."
    assert (quarantine / "fake_extractor.json").exists(), (
        "the evicted spec must be quarantined (preserved), not hard-deleted."
    )


@pytest.mark.asyncio
async def test_tool_with_a_trustworthy_win_is_kept(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """A learned tool with at least one trustworthy success (success=1,
    failure_class NULL) is kept even if it also has false wins."""
    learned = tmp_path / "learned"
    learned.mkdir()
    spec_path = _write_spec(learned, "good_tool")

    store = TaskOutcomeStore(tmp_db)
    await _record(store, "t0", "good_tool", success=False, failure_class="unachieved_effect")
    await _record(store, "t1", "good_tool", success=True, failure_class=None)  # a real win
    await _record(store, "t2", "good_tool", success=False, failure_class="unachieved_effect")

    report = await revalidate_learned_tools(
        tmp_db, learned, min_evidence=3, quarantine_dir=tmp_path / "q",
    )

    assert "good_tool" in report.kept
    assert "good_tool" not in report.evicted
    assert spec_path.exists(), "a tool with a genuine win must not be evicted."


@pytest.mark.asyncio
async def test_thin_evidence_tool_is_not_evicted(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """A learned tool with fewer than ``min_evidence`` attempts is left alone — too
    little history to condemn it."""
    learned = tmp_path / "learned"
    learned.mkdir()
    spec_path = _write_spec(learned, "new_tool")

    store = TaskOutcomeStore(tmp_db)
    await _record(store, "t0", "new_tool", success=False, failure_class="unachieved_effect")

    report = await revalidate_learned_tools(
        tmp_db, learned, min_evidence=3, quarantine_dir=tmp_path / "q",
    )

    assert "new_tool" in report.insufficient_evidence
    assert "new_tool" not in report.evicted
    assert spec_path.exists()


@pytest.mark.asyncio
async def test_dry_run_reports_without_evicting(
    tmp_db: DbPool, tmp_path: Path,
) -> None:
    """``dry_run`` surfaces suspects but leaves the live catalog untouched."""
    learned = tmp_path / "learned"
    learned.mkdir()
    spec_path = _write_spec(learned, "fake_extractor")

    store = TaskOutcomeStore(tmp_db)
    for i in range(3):
        await _record(store, f"t{i}", "fake_extractor", success=False,
                      failure_class="unachieved_effect")

    report = await revalidate_learned_tools(
        tmp_db, learned, min_evidence=3, dry_run=True, quarantine_dir=tmp_path / "q",
    )

    assert "fake_extractor" in report.suspects
    assert "fake_extractor" not in report.evicted, "dry_run must not evict."
    assert spec_path.exists(), "dry_run must leave the live spec in place."
