"""Tests for the write-safety substrate (E3-S2): UndoStore + UndoWriteTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.paths import StackowlHome
from stackowl.tools.io.undo_store import UndoStore, UndoWriteTool


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(StackowlHome, "home", classmethod(lambda cls: root))
    return root


@pytest.fixture
def ws(home: Path) -> Path:
    """The workspace (home/workspace) — restore targets must live inside it, since
    restore() now re-confines the write boundary (UndoStore restore-confinement)."""
    w = home / "workspace"
    w.mkdir(parents=True, exist_ok=True)
    return w


class TestUndoStore:
    def test_snapshot_then_restore_roundtrip(self, home: Path, ws: Path) -> None:
        store = UndoStore()
        target = ws / "f.txt"
        target.write_text("ORIGINAL")
        token = store.snapshot(target, "ORIGINAL")
        target.write_text("CHANGED")
        ok, msg = store.restore(token)
        assert ok is True
        assert target.read_text() == "ORIGINAL"
        assert "Restored" in msg

    def test_restore_target_outside_workspace_refused(self, home: Path, tmp_path: Path) -> None:
        # Defense-in-depth: even with a valid token, restore must refuse a target
        # that resolves outside the workspace (tampered index).
        store = UndoStore()
        outside = tmp_path / "outside.txt"
        outside.write_text("orig")
        token = store.snapshot(outside, "orig")
        outside.write_text("changed")
        ok, msg = store.restore(token)
        assert ok is False
        assert "workspace" in msg.lower()
        assert outside.read_text() == "changed"  # NOT overwritten

    def test_default_root_under_home(self, home: Path) -> None:
        store = UndoStore()
        assert store.root == home / "undo"

    def test_latest_token_tracks_most_recent(self, home: Path, ws: Path) -> None:
        store = UndoStore()
        f1, f2 = ws / "a.txt", ws / "b.txt"
        f1.write_text("a")
        f2.write_text("b")
        store.snapshot(f1, "a")
        t2 = store.snapshot(f2, "b")
        assert store.latest_token() == t2

    def test_bounded_ring_evicts_oldest(self, home: Path, ws: Path) -> None:
        store = UndoStore(max_snapshots=3)
        target = ws / "f.txt"
        target.write_text("x")
        tokens = [store.snapshot(target, f"v{i}") for i in range(5)]
        # Ring holds only the last 3; the two oldest are evicted.
        assert store.restore(tokens[0])[0] is False
        assert store.restore(tokens[1])[0] is False
        assert store.restore(tokens[4])[0] is True
        # Oldest blobs were deleted from disk too.
        blobs = list((home / "undo").glob("*.bak"))
        # tokens[4] just consumed by restore; tokens[2],[3] remain.
        assert len(blobs) == 2

    def test_restore_unknown_token_is_structured(self, home: Path) -> None:
        store = UndoStore()
        ok, msg = store.restore("does-not-exist")
        assert ok is False
        assert "Unknown undo token" in msg

    def test_restore_missing_blob_is_structured(self, home: Path) -> None:
        store = UndoStore()
        target = home / "f.txt"
        target.write_text("orig")
        token = store.snapshot(target, "orig")
        # Simulate a lost pre-image blob.
        (home / "undo" / f"{token}.bak").unlink()
        ok, msg = store.restore(token)
        assert ok is False
        assert "missing" in msg.lower()

    def test_corrupt_index_treated_as_empty(self, home: Path) -> None:
        store = UndoStore()
        (home / "undo").mkdir(parents=True)
        (home / "undo" / "index.json").write_text("{not json")
        assert store.latest_token() is None  # self-healing, no raise

    def test_consumed_snapshot_cannot_double_restore(self, home: Path, ws: Path) -> None:
        store = UndoStore()
        target = ws / "f.txt"
        target.write_text("orig")
        token = store.snapshot(target, "orig")
        assert store.restore(token)[0] is True
        # Second restore of the same token fails — it was consumed.
        assert store.restore(token)[0] is False


class TestUndoWriteTool:
    async def test_restores_most_recent(self, home: Path, ws: Path) -> None:
        store = UndoStore()
        target = ws / "f.txt"
        target.write_text("ORIGINAL")
        store.snapshot(target, "ORIGINAL")
        target.write_text("WRONG")
        result = await UndoWriteTool(store=store).execute()
        assert result.success is True
        assert target.read_text() == "ORIGINAL"

    async def test_specific_token(self, home: Path, ws: Path) -> None:
        store = UndoStore()
        target = ws / "f.txt"
        target.write_text("V0")
        token = store.snapshot(target, "V0")
        target.write_text("V1")
        result = await UndoWriteTool(store=store).execute(token=token)
        assert result.success is True
        assert target.read_text() == "V0"

    async def test_nothing_to_undo_is_structured(self, home: Path) -> None:
        result = await UndoWriteTool(store=UndoStore()).execute()
        assert result.success is False
        assert result.error is not None and "Nothing to undo" in result.error

    async def test_nothing_to_undo_is_not_an_effectful_failure(self, home: Path) -> None:
        """No snapshots exist — a pure no-op. Nothing was restored, so it must NOT
        count as an effectful failure that trips the give-up floor."""
        result = await UndoWriteTool(store=UndoStore()).execute()
        assert result.success is False
        assert result.side_effect_committed is False

    async def test_bad_token_is_structured_no_raise(self, home: Path) -> None:
        result = await UndoWriteTool(store=UndoStore()).execute(token="bogus")
        assert result.success is False
        assert result.error is not None and "Unknown undo token" in result.error

    async def test_successful_undo_stays_default_committed(self, home: Path, ws: Path) -> None:
        """Positive control: a real restore does not falsely clear the committed flag."""
        store = UndoStore()
        target = ws / "f.txt"
        target.write_text("ORIGINAL")
        store.snapshot(target, "ORIGINAL")
        target.write_text("WRONG")
        result = await UndoWriteTool(store=store).execute()
        assert result.success is True
        assert result.side_effect_committed is True
