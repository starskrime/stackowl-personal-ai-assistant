"""SUBST-1 / F091 — expand capability substitution beyond web_knowledge.

Adds a SECOND read-only capability class, ``file_read`` (read_file ⇄ pdf), both of
which take a ``path`` and return file contents. If one read-only file reader fails,
its read-only sibling can serve the same path — a safe, in-bounds, NON-consequential
substitution.

SECURITY INVARIANT (the F091 landmine): a ``consequential`` tool is NEVER eligible
for auto-substitution — the substitutable severity rank contains only read/write, so
a consequential sibling can never be auto-run (that would bypass the consent gate).
The final test asserts this by construction even when a consequential tool shares the
capability tag.
"""

from __future__ import annotations

from typing import Any

from stackowl.pipeline.capability_substitution import (
    build_args_for,
    find_substitute,
    normalized_input_for,
)
from stackowl.tools.registry import ToolRegistry


# --------------------------------------------------------------------------- #
# Adapters: read_file ⇄ pdf normalize to {"path": ...}
# --------------------------------------------------------------------------- #
def test_normalized_input_from_read_file() -> None:
    assert normalized_input_for("read_file", {"path": "doc.txt"}) == {"path": "doc.txt"}


def test_normalized_input_from_pdf() -> None:
    assert normalized_input_for("pdf", {"path": "report.pdf"}) == {"path": "report.pdf"}


def test_build_args_for_pdf_uses_path() -> None:
    assert build_args_for("pdf", {"path": "report.pdf"}) == {"path": "report.pdf"}


def test_build_args_for_read_file_uses_path() -> None:
    assert build_args_for("read_file", {"path": "doc.txt"}) == {"path": "doc.txt"}


def test_build_args_file_read_needs_path() -> None:
    assert build_args_for("pdf", {"path": ""}) is None


# --------------------------------------------------------------------------- #
# Both file readers carry the shared capability tag.
# --------------------------------------------------------------------------- #
def test_file_read_tools_tagged() -> None:
    reg = ToolRegistry.with_defaults()
    assert reg.get("read_file").manifest.capability_tag == "file_read"
    assert reg.get("pdf").manifest.capability_tag == "file_read"
    # both must be read-only (never consequential).
    assert reg.get("read_file").manifest.action_severity == "read"
    assert reg.get("pdf").manifest.action_severity == "read"


# --------------------------------------------------------------------------- #
# find_substitute picks the read-only sibling for a failed read_file.
# --------------------------------------------------------------------------- #
def test_find_substitute_routes_read_file_to_pdf() -> None:
    reg = ToolRegistry.with_defaults()
    result = find_substitute(
        "read_file",
        {"path": "report.pdf"},
        registry=reg,
        in_bounds=lambda _name: True,
        already_substituted=set(),
    )
    assert result is not None
    sibling, args = result
    assert sibling == "pdf"
    assert args == {"path": "report.pdf"}


# --------------------------------------------------------------------------- #
# THE LANDMINE GUARD — a consequential sibling sharing the tag is NEVER chosen.
# --------------------------------------------------------------------------- #
class _FakeManifest:
    def __init__(self, severity: str, tag: str | None) -> None:
        self.action_severity = severity
        self.capability_tag = tag


class _FakeTool:
    def __init__(self, name: str, severity: str, tag: str | None) -> None:
        self.name = name
        self.manifest = _FakeManifest(severity, tag)


class _FakeRegistry:
    def __init__(self, tools: list[_FakeTool]) -> None:
        self._tools = {t.name: t for t in tools}

    def get(self, name: str) -> Any:
        return self._tools.get(name)

    def all(self) -> list[Any]:
        return list(self._tools.values())


def test_consequential_sibling_never_substituted() -> None:
    # The failed tool and a CONSEQUENTIAL sibling share a capability tag. The
    # consequential sibling must NEVER be auto-substituted (consent-bypass landmine).
    reg = _FakeRegistry(
        [
            _FakeTool("read_file", "read", "file_read"),
            _FakeTool("delete_file", "consequential", "file_read"),
        ]
    )
    # Register a path adapter for the failed tool so normalization succeeds; the
    # consequential sibling has no read-only path to it regardless.
    result = find_substitute(
        "read_file",
        {"path": "x.txt"},
        registry=reg,
        in_bounds=lambda _name: True,
        already_substituted=set(),
    )
    # No read-only sibling exists → None. Crucially NOT the consequential tool.
    assert result is None or result[0] != "delete_file"
