"""Tests for EditTool (E3-S2) — fuzzy-assisted unique replace with verify + undo."""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.paths import StackowlHome
from stackowl.tools.io.edit import EditTool
from stackowl.tools.io.undo_store import UndoStore, UndoWriteTool


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    ws = home / "workspace"
    ws.mkdir(parents=True)
    monkeypatch.setattr(StackowlHome, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))
    return ws


class TestExactReplace:
    async def test_exact_replace(self, workspace: Path) -> None:
        f = workspace / "code.py"
        f.write_text("def foo():\n    return 1\n")
        result = await EditTool().execute(path=str(f), old_string="return 1", new_string="return 2")
        assert result.success is True
        assert f.read_text() == "def foo():\n    return 2\n"

    async def test_result_has_diff_and_undo_token(self, workspace: Path) -> None:
        f = workspace / "code.py"
        f.write_text("alpha\nbeta\n")
        result = await EditTool().execute(path=str(f), old_string="beta", new_string="gamma")
        assert result.success is True
        assert "Undo token:" in result.output
        assert "-beta" in result.output and "+gamma" in result.output  # unified diff body

    async def test_undo_token_restores_preimage(self, workspace: Path) -> None:
        store = UndoStore()
        f = workspace / "code.py"
        f.write_text("ORIGINAL\n")
        result = await EditTool(store=store).execute(path=str(f), old_string="ORIGINAL", new_string="CHANGED")
        assert result.success is True
        token = result.output.split("Undo token:")[1].split("\n")[0].strip()
        undo = await UndoWriteTool(store=store).execute(token=token)
        assert undo.success is True
        assert f.read_text() == "ORIGINAL\n"


class TestFuzzyReplace:
    async def test_whitespace_drift(self, workspace: Path) -> None:
        f = workspace / "code.py"
        # File has 4-space indent; the LLM quotes it with different leading space.
        f.write_text("class A:\n    def m(self):\n        return 1\n")
        result = await EditTool().execute(
            path=str(f),
            old_string="def m(self):\n  return 1",  # under-indented body
            new_string="def m(self):\n        return 2",
        )
        assert result.success is True
        assert "return 2" in f.read_text()


class TestNoMatch:
    async def test_no_match_gives_nearest_and_diff_hint(self, workspace: Path) -> None:
        f = workspace / "code.py"
        original = "def calculate_total(items):\n    return sum(items)\n"
        f.write_text(original)
        tool = EditTool()
        # Similar enough to surface a nearest candidate, different enough that no
        # fuzzy strategy treats it as a real match (line similarity < 0.80).
        result = await tool.execute(
            path=str(f), old_string="def handle_request(req):", new_string="x"
        )
        assert result.success is False
        assert result.error is not None
        assert "Nearest candidate" in result.error
        assert "similarity" in result.error
        assert "You quoted" in result.error  # char-diff hint
        assert f.read_text() == original  # untouched

    async def test_escalating_hint_on_repeated_failure(self, workspace: Path) -> None:
        f = workspace / "code.py"
        f.write_text("alpha\nbeta\n")
        tool = EditTool()
        r1 = await tool.execute(path=str(f), old_string="zzz_missing", new_string="x")
        r2 = await tool.execute(path=str(f), old_string="zzz_missing", new_string="x")
        assert r1.error is not None and "attempt" not in r1.error
        assert r2.error is not None and "[attempt 2]" in r2.error


class TestAmbiguous:
    async def test_multiple_matches_blocked_file_untouched(self, workspace: Path) -> None:
        f = workspace / "code.py"
        original = "x = 1\nx = 1\n"
        f.write_text(original)
        result = await EditTool().execute(path=str(f), old_string="x = 1", new_string="x = 2")
        assert result.success is False
        assert result.error is not None and "Found 2 matches" in result.error
        assert f.read_text() == original  # untouched, no partial write


class TestLineEndings:
    async def test_crlf_preserved(self, workspace: Path) -> None:
        f = workspace / "win.txt"
        f.write_bytes(b"line one\r\nline two\r\nline three\r\n")
        result = await EditTool().execute(path=str(f), old_string="line two", new_string="line TWO")
        assert result.success is True
        data = f.read_bytes()
        assert b"line TWO" in data
        assert b"\r\n" in data
        assert b"\n" not in data.replace(b"\r\n", b"")  # no bare LF introduced

    async def test_lf_preserved(self, workspace: Path) -> None:
        f = workspace / "unix.txt"
        f.write_bytes(b"a\nb\nc\n")
        result = await EditTool().execute(path=str(f), old_string="b", new_string="B")
        assert result.success is True
        assert f.read_bytes() == b"a\nB\nc\n"
        assert b"\r" not in f.read_bytes()


class TestPathGuard:
    async def test_path_escape_blocked(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path / "secret.txt"
        outside.write_text("secret")
        result = await EditTool().execute(path=str(outside), old_string="secret", new_string="leaked")
        assert result.success is False
        assert result.error is not None and "traversal" in result.error.lower()
        assert outside.read_text() == "secret"  # untouched


class TestSelfHealing:
    async def test_missing_file_is_structured(self, workspace: Path) -> None:
        result = await EditTool().execute(
            path=str(workspace / "nope.txt"), old_string="x", new_string="y"
        )
        assert result.success is False
        assert result.error is not None and "not found" in result.error.lower()

    async def test_empty_old_string_is_structured(self, workspace: Path) -> None:
        f = workspace / "code.py"
        f.write_text("data\n")
        result = await EditTool().execute(path=str(f), old_string="", new_string="y")
        assert result.success is False
        assert result.error is not None
        assert f.read_text() == "data\n"


class TestPostWriteVerify:
    async def test_verify_catches_silent_persistence_failure(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = workspace / "code.py"
        f.write_text("keep\nold\n")
        tool = EditTool()

        # Force read-back to return stale (unchanged) content → verify must fail.
        real_read_text = Path.read_text
        calls = {"n": 0}

        def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
            calls["n"] += 1
            # First call = initial read (real). Second = post-write verify (stale).
            if calls["n"] >= 2 and self == f:
                return "keep\nold\n"
            return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", fake_read_text)
        result = await tool.execute(path=str(f), old_string="old", new_string="new")
        assert result.success is False
        assert result.error is not None and "verification failed" in result.error.lower()
