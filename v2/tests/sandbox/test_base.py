"""Tests for the SandboxBackend ABC + package isolation invariant."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from stackowl.sandbox.base import SandboxAvailability, SandboxBackend


class TestSandboxBackendABC:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            SandboxBackend()  # type: ignore[abstract]

    def test_abstract_methods(self) -> None:
        names = SandboxBackend.__abstractmethods__
        assert {"name", "is_rootless", "supports_network", "is_available", "run"} <= names


class TestSandboxAvailability:
    def test_ok(self) -> None:
        a = SandboxAvailability.ok()
        assert a.available is True
        assert a.reason is None

    def test_no(self) -> None:
        a = SandboxAvailability.no("install bwrap")
        assert a.available is False
        assert a.reason == "install bwrap"


class TestPackageIsolation:
    def test_sandbox_never_imports_tools(self) -> None:
        """Trust-boundary invariant: the sandbox package MUST NOT import tools/."""
        import stackowl.sandbox as pkg

        pkg_dir = Path(inspect.getfile(pkg)).parent
        offenders: list[str] = []
        for path in pkg_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mod = None
                if isinstance(node, ast.ImportFrom):
                    mod = node.module
                elif isinstance(node, ast.Import):
                    mod = node.names[0].name if node.names else None
                if mod and (mod == "stackowl.tools" or mod.startswith("stackowl.tools.")):
                    offenders.append(f"{path.name}: {mod}")
        assert offenders == [], f"sandbox must not import tools/: {offenders}"
