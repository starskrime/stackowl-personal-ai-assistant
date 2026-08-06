"""Confinement tests — write_file/edit re-anchored to the SANDBOX workspace.

Asserts the single confinement primitive (path_guard) is correctly re-anchored to the
run's sandbox workspace by :func:`sandbox_write_root`, and that
:func:`confined_path_arg` rejects every escape (``..``, absolute outside, symlink)
while accepting a path inside the sandbox workspace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.paths import StackowlHome
from stackowl.sandbox.ptc.confine import (
    confined_path_arg,
    read_target_protected,
    sandbox_write_root,
)
from stackowl.tools.io.path_guard import data_root, is_within_root


class TestReadTargetProtected:
    """PTC read_file must NOT bulk-read the internal data stores (Vuln-1 fix)."""

    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):  # noqa: ANN202
        monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
        StackowlHome.ensure_exists()

    # Workspace-RELATIVE store paths only.
    #
    # "kuzu" and "skills" are deliberately NOT here. Both stores now live under
    # home() rather than under the workspace — skills moved out in D05.1, and
    # the graph was found on 2026-08-05 to have always been at home()/kuzu — so
    # no workspace-relative string names either of them, and asserting one only
    # tested that a non-existent path happened to be refused. Both are asserted
    # ABSOLUTELY below, against the accessor that resolves the real location,
    # which is strictly stronger.
    @pytest.mark.parametrize("p", ["stackowl.db", "lancedb", "lancedb/data.lance"])
    def test_internal_store_reads_are_protected(self, p: str) -> None:
        assert read_target_protected({"path": p}) is True

    def test_absolute_store_path_protected(self) -> None:
        assert read_target_protected({"path": str(StackowlHome.db_path())}) is True

    def test_the_REAL_skills_directory_is_protected(self) -> None:
        """Skills moved to home()/skills in D05.1. The relative "skills" param
        had been failing ever since — a red test carrying a security assertion,
        which is the worst kind to leave red."""
        assert read_target_protected({"path": str(StackowlHome.skills_dir())}) is True
        assert read_target_protected(
            {"path": str(StackowlHome.skills_dir() / "learned")}
        ) is True

    def test_the_REAL_knowledge_graph_is_protected(self) -> None:
        """The graph is the highest-value bulk-read target after the DB, and
        until 2026-08-05 it was NOT in the protected roots.

        _protected_roots() calls StackowlHome.kuzu_dir(), which used to return
        workspace/kuzu — an empty directory nothing had ever written — while
        MemoryAssembly opened home()/kuzu directly. So the guard protected the
        wrong path and the live 30MB graph was covered only incidentally, by
        data_root containment. Now the accessor and the assembly agree, and this
        asserts the store that actually holds data.
        """
        assert read_target_protected({"path": str(StackowlHome.kuzu_dir())}) is True
        assert read_target_protected(
            {"path": str(StackowlHome.kuzu_dir() / "graph.kuzu")}
        ) is True

    def test_ordinary_workspace_file_is_allowed(self) -> None:
        # A normal user/input file in the workspace is NOT a protected store → allowed.
        assert read_target_protected({"path": "downloads/input.csv"}) is False
        assert read_target_protected({"path": "notes.txt"}) is False

    def test_missing_path_not_protected_tool_rejects(self) -> None:
        assert read_target_protected({}) is False
        assert read_target_protected({"path": ""}) is False


class TestSandboxWriteRoot:
    def test_override_reanchors_data_root(self, tmp_path: Path) -> None:
        ws = tmp_path / "sandbox_ws"
        ws.mkdir()
        before = data_root()
        with sandbox_write_root(ws):
            assert data_root() == ws.resolve()
            # is_within_root now confines to the sandbox workspace, not the host one.
            assert is_within_root(ws / "out.txt")
            assert not is_within_root(tmp_path / "outside.txt")
        # restored on exit (no leak across calls).
        assert data_root() == before

    def test_override_resets_on_exception(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        before = data_root()
        try:
            with sandbox_write_root(ws):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert data_root() == before


class TestConfinedPathArg:
    def test_relative_path_anchored_inside(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        safe = confined_path_arg({"path": "sub/out.txt"}, ws)
        assert safe is not None
        assert safe.is_absolute()
        safe.resolve().relative_to(ws.resolve())

    def test_dotdot_escape_refused(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        assert confined_path_arg({"path": "../../etc/passwd"}, ws) is None

    def test_absolute_outside_refused(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        assert confined_path_arg({"path": "/etc/shadow"}, ws) is None

    def test_symlink_escape_refused(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = ws / "escape"
        link.symlink_to(outside)
        # A path THROUGH the symlink resolves outside the workspace → refused.
        assert confined_path_arg({"path": "escape/secret.txt"}, ws) is None

    def test_missing_path_refused(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        assert confined_path_arg({}, ws) is None
        assert confined_path_arg({"path": ""}, ws) is None
        assert confined_path_arg({"path": 123}, ws) is None
