"""Tests for search_files (E3-S1) — rg-first with a pure-Python walk fallback."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from stackowl.paths import StackowlHome
from stackowl.tools.io import search_files as sf
from stackowl.tools.io.search_files import SearchFilesTool


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def execute():\n    return 1\n")
    (root / "pkg" / "b.py").write_text("class Foo:\n    def execute(self):\n        pass\n")
    (root / "notes.txt").write_text("nothing here\n")
    # A dot-directory that must be pruned by the walk fallback.
    (root / ".git").mkdir()
    (root / ".git" / "config.py").write_text("def execute(): pass\n")
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: root))
    return root


def _force_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sf.shutil, "which", lambda _name: None)


class TestWalkFallback:
    async def test_content_search_finds_matches(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_walk(monkeypatch)
        result = await SearchFilesTool().execute(pattern=r"def execute", target="content")
        assert result.success is True
        assert "engine=walk" in result.output
        assert "a.py" in result.output and "b.py" in result.output

    async def test_dot_dirs_pruned(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_walk(monkeypatch)
        result = await SearchFilesTool().execute(pattern=r"def execute", target="content")
        assert ".git" not in result.output  # pruned, never searched

    async def test_files_search_glob(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_walk(monkeypatch)
        result = await SearchFilesTool().execute(pattern="*.py", target="files")
        assert result.success is True
        assert "a.py" in result.output and "b.py" in result.output
        assert "notes.txt" not in result.output

    async def test_file_glob_filter(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_walk(monkeypatch)
        result = await SearchFilesTool().execute(pattern="execute", target="content", file_glob="a.py")
        assert "a.py" in result.output
        assert "b.py" not in result.output

    async def test_max_results_truncates(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_walk(monkeypatch)
        result = await SearchFilesTool().execute(pattern="execute", target="content", max_results=1)
        assert "showing 1 of" in result.output
        assert "narrow" in result.output  # truncation hint

    async def test_invalid_regex_structured_error(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_walk(monkeypatch)
        result = await SearchFilesTool().execute(pattern="(unclosed", target="content")
        assert result.success is False
        assert "regex" in (result.error or "").lower()


class TestGuardAndLoop:
    async def test_path_escape_denied(self, tree: Path) -> None:
        result = await SearchFilesTool().execute(pattern="x", path="../../etc")
        assert result.success is False
        assert "traversal" in (result.error or "").lower()

    async def test_consecutive_loop_detection(self, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _force_walk(monkeypatch)
        tool = SearchFilesTool()
        for _ in range(3):
            r = await tool.execute(pattern="execute", target="content")
            assert "BLOCKED" not in r.output
        r4 = await tool.execute(pattern="execute", target="content")
        assert "BLOCKED" in r4.output  # 4th identical search → guidance

    async def test_missing_pattern(self, tree: Path) -> None:
        result = await SearchFilesTool().execute(pattern="")
        assert result.success is False


class TestRgJsonParse:
    """Direct coverage of the rg --json parser — covers the path the CI skip hides
    (and the Windows drive-letter case that broke the old colon-split, C1)."""

    def test_match_event_parsed(self) -> None:
        raw = '{"type":"match","data":{"path":{"text":"/ws/a.py"},"lines":{"text":"def x():\\n"},"line_number":12}}'
        m = SearchFilesTool._parse_rg_json(raw)
        assert m is not None
        assert m.line == 12
        assert "def x()" in m.text

    def test_windows_drive_letter_path_not_dropped(self) -> None:
        raw = '{"type":"match","data":{"path":{"text":"C:\\\\ws\\\\a.py"},"lines":{"text":"hit\\n"},"line_number":3}}'
        m = SearchFilesTool._parse_rg_json(raw)
        assert m is not None  # would have been dropped by a ':'-split parser
        assert m.line == 3

    def test_non_match_event_ignored(self) -> None:
        assert SearchFilesTool._parse_rg_json('{"type":"begin","data":{}}') is None
        assert SearchFilesTool._parse_rg_json("not json") is None


class TestWalkSymlinkConfinement:
    async def test_symlinked_file_outside_root_not_read(
        self, tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _force_walk(monkeypatch)
        secret = tmp_path / "outside_secret.txt"
        secret.write_text("def execute(): TOPSECRET\n")
        link = tree / "leak.txt"
        try:
            link.symlink_to(secret)
        except OSError:
            pytest.skip("symlinks unsupported")
        result = await SearchFilesTool().execute(pattern="TOPSECRET", target="content")
        assert "TOPSECRET" not in result.output  # symlink escape not read (M2)


class TestRipgrepPath:
    @pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
    async def test_rg_content_search(self, tree: Path) -> None:
        result = await SearchFilesTool().execute(pattern=r"def execute", target="content")
        assert result.success is True
        assert "engine=rg" in result.output
        assert "a.py" in result.output


class TestRegistry:
    def test_registered_read_severity(self) -> None:
        from stackowl.tools.registry import ToolRegistry

        tool = ToolRegistry.with_defaults().get("search_files")
        assert tool is not None
        assert tool.manifest.action_severity == "read"
