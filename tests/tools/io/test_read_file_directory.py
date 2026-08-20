"""read_file on a DIRECTORY is an answerable mistake, not a platform fault.

FOUND ON LIVE TRAFFIC 2026-08-20, watching a real turn of Bakir's:

    ERROR read_file.execute: OS error {'path': '/home/boss/.stackowl/workspace'}
      IsADirectoryError: [Errno 21] Is a directory
    ERROR read_file.execute: OS error   (again, seven minutes later)
    WARNING [pipeline] execute: same-tool failure threshold reached — circuit open
      {'tool': 'read_file', 'threshold': 3, 'deterministic': True}

Two things were wrong and they compound.

THE MODEL WAS TOLD WHAT HAPPENED AND NOT WHAT TO DO. "[Errno 21] Is a directory"
is a diagnosis with no next step, so the model reissued the identical call until the
circuit breaker cut it off — three wasted model rounds inside a turn the user was
waiting on. That is the same shape 021cd0aa fixed for repeated tool failures: a
component that reports THAT something failed and never what to do about it is
undiagnosable exactly when it matters.

AND IT WAS LOGGED AT ERROR WITH A TRACEBACK. A model guessing a path is ordinary;
the filesystem failing is not. Logging both identically means the level carries no
information, and a real OS fault hides among the guesses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.tools.io.read_file import ReadFileTool


@pytest.fixture
def _in_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the path guard at a temp tree so the read is allowed, not confined."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setenv("STACKOWL_DATA_DIR", str(tmp_path))
    return tmp_path


class TestADirectoryIsAnsweredNotThrown:
    async def test_the_error_names_the_next_step(self, _in_workspace: Path) -> None:
        """The fix is the SECOND sentence. Without it the model has nothing to try
        and repeats the call until the breaker opens."""
        target = _in_workspace / "somedir"
        target.mkdir()

        result = await ReadFileTool()(path=str(target))

        assert result.success is False
        assert "is a directory" in result.error.lower()
        assert "search_files" in result.error, (
            "an error with no next step is what produced three identical retries"
        )

    async def test_it_is_not_logged_as_a_platform_fault(
        self, _in_workspace: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ERROR here should still mean the filesystem is in trouble."""
        target = _in_workspace / "somedir"
        target.mkdir()

        with caplog.at_level("WARNING", logger="stackowl.tool"):
            await ReadFileTool()(path=str(target))

        levels = {r.levelname for r in caplog.records if "read_file" in r.message}
        assert "ERROR" not in levels, f"a directory read logged as an ERROR: {levels}"

    async def test_a_real_file_is_unaffected(self, _in_workspace: Path) -> None:
        target = _in_workspace / "note.txt"
        target.write_text("hello", encoding="utf-8")

        result = await ReadFileTool()(path=str(target))

        assert result.success is True
        assert result.output == "hello"

    async def test_a_missing_path_still_says_not_found(self, _in_workspace: Path) -> None:
        """The directory branch must not swallow the case it sits next to."""
        result = await ReadFileTool()(path=str(_in_workspace / "nope.txt"))

        assert result.success is False
        assert "not found" in result.error.lower()
