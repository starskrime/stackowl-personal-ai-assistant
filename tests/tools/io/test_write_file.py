"""Tests for WriteFileTool — workspace-anchored writes with a traversal guard (H2).

A relative path resolves UNDER the workspace (not the process CWD), the same way
``send_file``/``shell`` anchor file I/O, while the shared path-confinement guard
still rejects an absolute path outside the workspace and a ``..`` traversal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.paths import StackowlHome
from stackowl.tools.base import ToolResult
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


async def test_successful_write_names_its_artifact(workspace: Path) -> None:
    """A real write exposes its structured artifact_path so the verify() seam can
    observe it (no re-parsing of free output text)."""
    result = await WriteFileTool().execute(path="ok.txt", content="x")
    assert result.success is True
    assert result.artifact_path == str(workspace / "ok.txt")


async def test_write_verifies_true_through_call_seam(workspace: Path) -> None:
    """Through __call__, a real non-empty write is OBSERVED → verified True."""
    result = await WriteFileTool()(path="ok.txt", content="content")
    assert result.success is True
    assert result.verified is True


async def test_write_of_empty_content_is_not_trustworthy(workspace: Path) -> None:
    """A zero-byte write claims success but produced no real artifact → verified False
    (the verify_artifact non-empty rule). success is preserved, trust is not."""
    from stackowl.tools.verification import is_trustworthy_success

    result = await WriteFileTool()(path="blank.txt", content="")
    assert result.success is True
    assert result.verified is False
    assert is_trustworthy_success(result.success, result.verified) is False


async def test_verify_false_on_truncated_write(workspace: Path) -> None:
    """F-33: verify() reads the file back and compares it to the intended content.
    A SHORT/TRUNCATED persisted write (right path, fresh, non-empty) — fewer bytes
    than were claimed — must fail verification, not pass on existence alone."""
    target = workspace / "doc.txt"
    target.write_text("short", encoding="utf-8")  # only 5 bytes actually landed...
    result = ToolResult(
        success=True, output="", duration_ms=1.0, artifact_path=str(target)
    )
    # ...but the call CLAIMED to write a far longer body.
    verdict = await WriteFileTool().verify(
        {"path": "doc.txt", "content": "short" + "x" * 200},
        result,
        started_at=target.stat().st_mtime - 1,
    )
    assert verdict is False


async def test_verify_false_on_divergent_write(workspace: Path) -> None:
    """F-33: same length, different bytes — on-disk content that does not match the
    intended content must fail (length parity alone is not sufficient)."""
    target = workspace / "doc.txt"
    target.write_text("AAAAA", encoding="utf-8")
    result = ToolResult(
        success=True, output="", duration_ms=1.0, artifact_path=str(target)
    )
    verdict = await WriteFileTool().verify(
        {"path": "doc.txt", "content": "BBBBB"},
        result,
        started_at=target.stat().st_mtime - 1,
    )
    assert verdict is False


async def test_verify_true_on_exact_write(workspace: Path) -> None:
    """F-33 positive control: when the on-disk bytes equal the intended content the
    read-back compare passes (verify True)."""
    target = workspace / "doc.txt"
    body = "the full intended body\nwith two lines"
    target.write_text(body, encoding="utf-8")
    result = ToolResult(
        success=True, output="", duration_ms=1.0, artifact_path=str(target)
    )
    verdict = await WriteFileTool().verify(
        {"path": "doc.txt", "content": body},
        result,
        started_at=target.stat().st_mtime - 1,
    )
    assert verdict is True
