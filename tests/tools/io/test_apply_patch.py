"""Tests for apply_patch (E3-S3) — atomic V4A multi-file patch with rollback.

Covers: single-file update; multi-file patch; context mismatch → full rollback
(NO partial write); Add over existing → error; Delete missing → error; the
party-mandated escape test (Add/Delete target outside the workspace → structured
error, nothing written); oversized patch → structured error; undo after a
successful patch; and self-healing (malformed patch → structured, no raise).

Targets live UNDER the workspace because UndoStore.restore() confines its write
boundary to the workspace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.paths import StackowlHome
from stackowl.tools.io.apply_patch import ApplyPatchTool
from stackowl.tools.io.undo_store import UndoStore, UndoWriteTool


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(StackowlHome, "home", classmethod(lambda cls: root))
    return root


@pytest.fixture
def ws(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    w = home / "workspace"
    w.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: w))
    return w


def _patch(*body: str) -> str:
    return "*** Begin Patch\n" + "\n".join(body) + "\n*** End Patch\n"


class TestHappyPath:
    async def test_single_file_update(self, home: Path, ws: Path) -> None:
        f = ws / "a.py"
        f.write_text("def foo():\n    return 1\n")
        patch = _patch(
            "*** Update File: " + str(f),
            "@@",
            " def foo():",
            "-    return 1",
            "+    return 2",
        )
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is True, result.error
        assert f.read_text() == "def foo():\n    return 2\n"
        assert "Undo token:" in result.output

    async def test_multi_file_both_change(self, home: Path, ws: Path) -> None:
        a = ws / "a.txt"
        b = ws / "b.txt"
        a.write_text("alpha\n")
        b.write_text("beta\n")
        patch = _patch(
            "*** Update File: " + str(a),
            "@@",
            "-alpha",
            "+ALPHA",
            "*** Update File: " + str(b),
            "@@",
            "-beta",
            "+BETA",
        )
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is True, result.error
        assert a.read_text() == "ALPHA\n"
        assert b.read_text() == "BETA\n"

    async def test_add_new_file(self, home: Path, ws: Path) -> None:
        new = ws / "sub" / "new.txt"
        patch = _patch(
            "*** Add File: " + str(new),
            "+hello",
            "+world",
        )
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is True, result.error
        assert new.read_text() == "hello\nworld"

    async def test_delete_file(self, home: Path, ws: Path) -> None:
        f = ws / "gone.txt"
        f.write_text("bye\n")
        patch = _patch("*** Delete File: " + str(f))
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is True, result.error
        assert not f.exists()


class TestAtomicRollback:
    async def test_context_mismatch_rolls_back_all_no_partial_write(
        self, home: Path, ws: Path
    ) -> None:
        # First file's hunk applies cleanly; second file's hunk cannot match.
        # The whole patch must roll back — the FIRST file must be untouched too.
        a = ws / "a.txt"
        b = ws / "b.txt"
        a.write_text("alpha\n")
        b.write_text("beta\n")
        patch = _patch(
            "*** Update File: " + str(a),
            "@@",
            "-alpha",
            "+ALPHA",
            "*** Update File: " + str(b),
            "@@",
            "-THIS_LINE_DOES_NOT_EXIST",
            "+whatever",
        )
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.error is not None and "rolled back" in result.error.lower()
        # NEITHER file changed.
        assert a.read_text() == "alpha\n"
        assert b.read_text() == "beta\n"

    async def test_rollback_removes_created_file_when_later_op_fails(
        self, home: Path, ws: Path
    ) -> None:
        created = ws / "created.txt"
        existing = ws / "existing.txt"
        existing.write_text("keep\n")
        patch = _patch(
            "*** Add File: " + str(created),
            "+new content",
            "*** Update File: " + str(existing),
            "@@",
            "-NOPE",
            "+x",
        )
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        # The created file must have been removed by rollback.
        assert not created.exists()
        assert existing.read_text() == "keep\n"


class TestErrors:
    async def test_add_over_existing_errors(self, home: Path, ws: Path) -> None:
        f = ws / "exists.txt"
        f.write_text("already here\n")
        patch = _patch("*** Add File: " + str(f), "+overwrite me")
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.error is not None and "already exists" in result.error
        # Original untouched.
        assert f.read_text() == "already here\n"

    async def test_delete_missing_errors(self, home: Path, ws: Path) -> None:
        patch = _patch("*** Delete File: " + str(ws / "nope.txt"))
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.error is not None and "not found" in result.error

    async def test_add_target_escaping_workspace_blocked_nothing_written(
        self, home: Path, ws: Path, tmp_path: Path
    ) -> None:
        # Party-mandated escape test: an Add target outside the workspace must be
        # refused BEFORE any write, with nothing created.
        escape = tmp_path / "escape.txt"
        patch = _patch("*** Add File: " + str(escape), "+pwned")
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.error is not None and "outside the workspace" in result.error
        assert not escape.exists()

    async def test_delete_target_escaping_workspace_blocked(
        self, home: Path, ws: Path, tmp_path: Path
    ) -> None:
        outside = tmp_path / "victim.txt"
        outside.write_text("precious\n")
        patch = _patch("*** Delete File: " + str(outside))
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.error is not None and "outside the workspace" in result.error
        # Untouched — guard fired before any IO.
        assert outside.read_text() == "precious\n"

    async def test_traversal_escape_via_dotdot_blocked(
        self, home: Path, ws: Path
    ) -> None:
        # A '..' traversal inside the path string must also be refused.
        target = str(ws / ".." / "outside.txt")
        patch = _patch("*** Add File: " + target, "+x")
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.error is not None and "outside the workspace" in result.error

    async def test_oversized_patch_rejected(self, home: Path, ws: Path) -> None:
        huge = "x" * (3 * 1024 * 1024)
        patch = _patch("*** Add File: " + str(ws / "big.txt"), "+" + huge)
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.error is not None and "too large" in result.error.lower()

    async def test_malformed_patch_is_structured_no_raise(self, home: Path, ws: Path) -> None:
        # UPDATE with no hunks → parser returns a structured parse error.
        patch = _patch("*** Update File: " + str(ws / "x.txt"))
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.error is not None and "parse failed" in result.error.lower()

    async def test_empty_patch_is_structured(self, home: Path, ws: Path) -> None:
        result = await ApplyPatchTool(store=UndoStore()).execute(patch="")
        assert result.success is False
        assert result.error is not None and "missing patch" in result.error.lower()


class TestUndo:
    async def test_undo_restores_after_successful_patch(self, home: Path, ws: Path) -> None:
        store = UndoStore()
        f = ws / "a.txt"
        f.write_text("original\n")
        patch = _patch(
            "*** Update File: " + str(f),
            "@@",
            "-original",
            "+modified",
        )
        result = await ApplyPatchTool(store=store).execute(patch=patch)
        assert result.success is True, result.error
        assert f.read_text() == "modified\n"
        # undo_write (sharing the same store) reverts the last write.
        undo = await UndoWriteTool(store=store).execute()
        assert undo.success is True, undo.error
        assert f.read_text() == "original\n"

    async def test_round_trip_with_edit(self, home: Path, ws: Path) -> None:
        from stackowl.tools.io.edit import EditTool

        store = UndoStore()
        f = ws / "rt.txt"
        f.write_text("one\ntwo\nthree\n")
        # Patch changes line 2.
        patch = _patch(
            "*** Update File: " + str(f),
            "@@",
            "-two",
            "+TWO",
        )
        pr = await ApplyPatchTool(store=store).execute(patch=patch)
        assert pr.success is True, pr.error
        # edit changes line 3 (shared store keeps undo coherent).
        er = await EditTool(store=store).execute(path=str(f), old_string="three", new_string="THREE")
        assert er.success is True, er.error
        assert f.read_text() == "one\nTWO\nTHREE\n"


class TestMove:
    async def test_move_file(self, home: Path, ws: Path) -> None:
        src = ws / "old.txt"
        dst = ws / "new.txt"
        src.write_text("content\n")
        patch = _patch("*** Move File: " + str(src) + " -> " + str(dst))
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is True, result.error
        assert not src.exists()
        assert dst.read_text() == "content\n"

    async def test_move_dest_escaping_workspace_blocked(
        self, home: Path, ws: Path, tmp_path: Path
    ) -> None:
        src = ws / "src.txt"
        src.write_text("data\n")
        dst = tmp_path / "exfil.txt"
        patch = _patch("*** Move File: " + str(src) + " -> " + str(dst))
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.error is not None and "outside the workspace" in result.error
        # Source untouched, dest never created.
        assert src.read_text() == "data\n"
        assert not dst.exists()


class TestRollbackBeyondRing:
    async def test_patch_larger_than_ring_rolls_back_all(self, home: Path, ws: Path) -> None:
        # C1 regression: rollback must restore EVERY modified file even when the
        # patch touches more files than the UndoStore ring holds — atomicity uses
        # the in-memory pre-image buffer, not the evictable ring.
        n = 5
        files = []
        for i in range(n):
            f = ws / f"f{i}.txt"
            f.write_text(f"orig{i}\n")
            files.append(f)
        body: list[str] = []
        for i in range(n - 1):  # first n-1 updates are valid
            body += ["*** Update File: " + str(files[i]), "@@", f"-orig{i}", f"+MOD{i}"]
        # Last op fails (context cannot match) → whole patch rolls back.
        body += ["*** Update File: " + str(files[-1]), "@@", "-NOPE_NO_MATCH", "+x"]
        patch = _patch(*body)
        # Ring smaller than the number of modified files — would lose pre-images
        # under the old per-token rollback.
        result = await ApplyPatchTool(store=UndoStore(max_snapshots=2)).execute(patch=patch)
        assert result.success is False
        for i in range(n):
            assert files[i].read_text() == f"orig{i}\n", f"f{i} not rolled back"


class TestFullMultiFileUndo:
    async def test_single_undo_reverts_entire_patch(self, home: Path, ws: Path) -> None:
        # M1 regression: one undo_write reverts ALL files of a multi-file patch,
        # including removing a file the patch CREATED.
        store = UndoStore()
        a = ws / "a.txt"
        b = ws / "b.txt"
        a.write_text("alpha\n")
        b.write_text("beta\n")
        created = ws / "c.txt"
        patch = _patch(
            "*** Update File: " + str(a), "@@", "-alpha", "+ALPHA",
            "*** Update File: " + str(b), "@@", "-beta", "+BETA",
            "*** Add File: " + str(created), "+new content",
        )
        result = await ApplyPatchTool(store=store).execute(patch=patch)
        assert result.success is True, result.error
        assert a.read_text() == "ALPHA\n" and b.read_text() == "BETA\n" and created.exists()
        # Extract the group undo token from the result.
        token = result.output.split("Undo token:", 1)[1].split()[0]
        ok, msg = store.restore(token)
        assert ok is True, msg
        assert a.read_text() == "alpha\n"  # reverted
        assert b.read_text() == "beta\n"  # reverted
        assert not created.exists()  # created file removed by undo


class TestSideEffectCommittedHonesty:
    """A pre-execution refusal (bad/oversized/unparseable patch, no ops, traversal)
    leaves nothing locked or written → side_effect_committed must be False so it
    does not trip the give-up floor. A mid-apply failure (rolled back) ATTEMPTED
    writes → must stay True (positive control)."""

    async def test_missing_patch_not_effectful(self, home: Path, ws: Path) -> None:
        result = await ApplyPatchTool(store=UndoStore()).execute(patch="")
        assert result.success is False
        assert result.side_effect_committed is False

    async def test_parse_error_not_effectful(self, home: Path, ws: Path) -> None:
        result = await ApplyPatchTool(store=UndoStore()).execute(patch="not a real patch")
        assert result.success is False
        assert result.side_effect_committed is False

    async def test_traversal_refusal_not_effectful(
        self, home: Path, ws: Path, tmp_path: Path
    ) -> None:
        escape = tmp_path / "escape.txt"
        patch = _patch("*** Add File: " + str(escape), "+pwned")
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.side_effect_committed is False
        assert not escape.exists()

    async def test_rolled_back_apply_stays_committed(self, home: Path, ws: Path) -> None:
        """Positive control: a mid-apply failure rolled back the writes it had
        already issued — the boundary was crossed, so committed stays True."""
        a = ws / "a.txt"
        b = ws / "b.txt"
        a.write_text("alpha\n")
        b.write_text("beta\n")
        patch = _patch(
            "*** Update File: " + str(a),
            "@@",
            "-alpha",
            "+ALPHA",
            "*** Update File: " + str(b),
            "@@",
            "-THIS_LINE_DOES_NOT_EXIST",
            "+whatever",
        )
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is False
        assert result.side_effect_committed is True

    async def test_successful_patch_stays_committed(self, home: Path, ws: Path) -> None:
        f = ws / "a.txt"
        f.write_text("alpha\n")
        patch = _patch("*** Update File: " + str(f), "@@", "-alpha", "+ALPHA")
        result = await ApplyPatchTool(store=UndoStore()).execute(patch=patch)
        assert result.success is True
        assert result.side_effect_committed is True
