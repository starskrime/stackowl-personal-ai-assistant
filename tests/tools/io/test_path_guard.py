"""Tests for the shared filesystem path-confinement guard (E3 substrate).

This primitive gates every file-touching tool (read/write/search/edit/patch), so
it gets direct coverage independent of any one tool.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.paths import StackowlHome
from stackowl.tools.io import path_guard


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(StackowlHome, "workspace", classmethod(lambda cls: root))
    return root


class TestIsWithinRoot:
    def test_file_inside_root_allowed(self, workspace: Path) -> None:
        (workspace / "sub").mkdir()
        assert path_guard.is_within_root(workspace / "sub" / "f.txt") is True

    def test_root_itself_allowed(self, workspace: Path) -> None:
        assert path_guard.is_within_root(workspace) is True

    def test_parent_escape_blocked(self, workspace: Path) -> None:
        assert path_guard.is_within_root(workspace / ".." / "secret.txt") is False

    def test_absolute_outside_blocked(self, workspace: Path) -> None:
        assert path_guard.is_within_root(Path("/etc/passwd")) is False

    def test_symlink_escape_blocked(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("x")
        link = workspace / "link"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlinks unsupported on this platform")
        # The symlink resolves outside the root → blocked (resolve() follows it).
        assert path_guard.is_within_root(link / "secret.txt") is False

    def test_data_root_resolves_to_workspace(self, workspace: Path) -> None:
        assert path_guard.data_root() == workspace.resolve()


class TestResolveInWorkspace:
    """The shared anchor used by read/write/edit/apply_patch (round-trip fix).

    A RELATIVE path — exactly what search_files emits as a hit path — resolves
    UNDER the workspace, so a search hit piped straight into read/edit lands on
    the right file. An ABSOLUTE path is returned untouched. The guard still
    confines whatever comes out, so a genuine escape is still denied.
    """

    def test_relative_anchors_under_workspace(self, workspace: Path) -> None:
        # search_files renders hits like "src/classifier.py" — anchor under ws.
        resolved = path_guard.resolve_in_workspace("src/classifier.py")
        assert resolved == workspace / "src" / "classifier.py"
        assert path_guard.is_within_root(resolved) is True

    def test_absolute_returned_unchanged(self, workspace: Path) -> None:
        abs_in = workspace / "src" / "classifier.py"
        assert path_guard.resolve_in_workspace(str(abs_in)) == abs_in

    def test_relative_escape_still_denied_by_guard(self, workspace: Path) -> None:
        # Anchoring does not weaken confinement: a "../" relative path resolves
        # under ws textually but climbs out — the guard catches it.
        resolved = path_guard.resolve_in_workspace("../secret.txt")
        assert path_guard.is_within_root(resolved) is False

    def test_absolute_outside_still_denied_by_guard(self, workspace: Path) -> None:
        resolved = path_guard.resolve_in_workspace("/etc/passwd")
        assert path_guard.is_within_root(resolved) is False


class TestSharedBySiblings:
    def test_read_and_write_import_the_same_guard(self) -> None:
        from stackowl.tools.io import read_file, write_file

        # Both tools alias the shared guard — one source of truth (party E3 #1).
        assert read_file._guard is path_guard.is_within_root
        assert write_file._guard is path_guard.is_within_root

    def test_io_tools_share_the_same_resolver(self) -> None:
        from stackowl.tools.io import apply_patch, edit, read_file, write_file

        # Every path-taking io tool anchors through the ONE shared resolver, so a
        # relative search hit round-trips identically across read/edit/patch/write.
        assert read_file._resolve is path_guard.resolve_in_workspace
        assert write_file._resolve is path_guard.resolve_in_workspace
        assert edit._resolve is path_guard.resolve_in_workspace
        assert apply_patch._resolve is path_guard.resolve_in_workspace
