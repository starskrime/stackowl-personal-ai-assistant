"""F-31 — ShellTool reads back a named output file instead of trusting exit-0.

A shell command can exit 0 yet produce nothing. ``returncode == 0`` stays the
``success`` self-report, but when the command UNAMBIGUOUSLY redirects stdout to a
named file (``cmd > out.txt``) the tool now records that path as ``artifact_path``
and a ``verify()`` confirms it exists / is non-empty / is this run's artifact. A
command with no named output file leaves ``verified`` as ``None`` (never
over-claims). ``-o``/``--output`` flags are a documented deferral (too often a
non-path option value to verify safely).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.tools.system.shell import ShellTool, _redirect_target


# --------------------------------------------------------------------------- #
# _redirect_target — conservative stdout-redirection extraction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["echo", "hi", ">", "out.txt"], "out.txt"),
        (["echo", "hi", ">>", "out.log"], "out.log"),
        (["echo", "hi", ">out.txt"], "out.txt"),
        (["echo", "hi", "1>", "out.txt"], "out.txt"),
        (["echo", "hi", "1>>out.txt"], "out.txt"),
        # last redirect wins (shell semantics)
        (["cmd", ">", "a.txt", ">", "b.txt"], "b.txt"),
    ],
)
def test_redirect_target_extracted(argv: list[str], expected: str) -> None:
    assert _redirect_target(argv) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["echo", "hi"],                      # no redirect
        ["ssh", "-o", "StrictHostKeyChecking=no", "host"],  # -o is NOT a path (deferral)
        ["cmd", "2>", "err.log"],            # stderr — may legitimately be empty
        ["cmd", "2>err.log"],
        ["cmd", "&>", "all.log"],            # combined — skipped
        ["cmd", ">&2"],                      # fd-dup, not a file
        ["cmd", ">", "/dev/null"],           # device sink, not an artifact
    ],
)
def test_redirect_target_none(argv: list[str]) -> None:
    assert _redirect_target(argv) is None


# --------------------------------------------------------------------------- #
# verify() end-to-end via __call__ (the seam that runs verify)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_redirect_to_nonempty_file_verifies_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    result = await ShellTool()(command="echo hello > out.txt", workdir=str(tmp_path))

    assert result.success is True, result.error
    assert result.verified is True
    assert result.artifact_path == str(tmp_path / "out.txt")
    assert (tmp_path / "out.txt").read_text().strip() == "hello"


@pytest.mark.asyncio
async def test_exit_zero_but_empty_output_file_verifies_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The core F-31 case: command exits 0 but the named file is empty."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    result = await ShellTool()(command="true > empty.txt", workdir=str(tmp_path))

    assert result.success is True  # self-report preserved
    assert result.verified is False  # reality disagrees — nothing was produced
    assert (tmp_path / "empty.txt").exists()
    assert (tmp_path / "empty.txt").stat().st_size == 0


@pytest.mark.asyncio
async def test_no_redirect_leaves_verified_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A generic command names no output file ⇒ never over-claim verification."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    result = await ShellTool()(command="echo hi", workdir=str(tmp_path))

    assert result.success is True, result.error
    assert result.verified is None
    assert result.artifact_path is None
