"""Tests for WriteFileTool — workspace-anchored writes with a traversal guard (H2).

A relative path resolves UNDER the workspace (not the process CWD), the same way
``send_file``/``shell`` anchor file I/O, while the shared path-confinement guard
still rejects an absolute path outside the workspace and a ``..`` traversal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.paths import StackowlHome
from stackowl.tools.io.write_file import WriteFileTool

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: ws))
    return ws


async def test_relative_path_anchored_to_workspace(workspace: Path) -> None:
    result = await WriteFileTool().execute(path="notes/out.txt", content="hi")
    assert result.success is True, result.error
    landed = workspace / "notes" / "out.txt"
    assert landed.exists()  # resolved UNDER the workspace, not the process CWD
    assert landed.read_text() == "hi"


async def test_bare_relative_name_lands_in_workspace(workspace: Path) -> None:
    result = await WriteFileTool().execute(path="report.md", content="x")
    assert result.success is True
    assert (workspace / "report.md").read_text() == "x"


async def test_absolute_outside_workspace_denied(
    workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    result = await WriteFileTool().execute(path=str(outside), content="nope")
    assert result.success is False
    assert "traversal" in (result.error or "").lower()
    assert not outside.exists()  # nothing written outside the workspace


async def test_parent_traversal_denied(workspace: Path) -> None:
    escaping = str(workspace / ".." / "escape.txt")
    result = await WriteFileTool().execute(path=escaping, content="nope")
    assert result.success is False
    assert "traversal" in (result.error or "").lower()
    assert not (workspace.parent / "escape.txt").exists()


async def test_absolute_inside_workspace_allowed(workspace: Path) -> None:
    inside = workspace / "sub" / "f.txt"
    result = await WriteFileTool().execute(path=str(inside), content="ok")
    assert result.success is True
    assert inside.read_text() == "ok"


async def test_traversal_refusal_is_not_an_effectful_failure(workspace: Path) -> None:
    """A traversal-denied write is a PRE-EXEC refusal — nothing was written, so it
    must NOT count as an effectful failure (else it wrongly trips the give-up floor)."""
    escaping = str(workspace / ".." / "escape.txt")
    result = await WriteFileTool().execute(path=escaping, content="nope")
    assert result.success is False
    assert result.side_effect_committed is False  # nothing crossed the boundary


async def test_successful_write_stays_default_committed(workspace: Path) -> None:
    """Positive control: a genuine write does not falsely clear the committed flag
    (success=True makes the field irrelevant, but assert the value is unchanged)."""
    result = await WriteFileTool().execute(path="ok.txt", content="x")
    assert result.success is True
    assert result.side_effect_committed is True
