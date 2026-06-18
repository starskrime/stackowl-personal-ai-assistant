#!/usr/bin/env python3
"""B8: Embedding network enforcement — no external HTTP imports in embeddings/.

The ``stackowl.embeddings`` package is the local-only embedding contract.
Allowing any HTTP-capable library to land here would silently break the
self-hosted guarantee, so this boundary script walks every ``*.py`` file
under ``src/stackowl/embeddings`` and rejects any forbidden ``import`` or
``from ... import`` statement at AST level.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_FORBIDDEN_IMPORTS: frozenset[str] = frozenset(
    {
        "openai",
        "anthropic",
        "google",  # blocks google.genai, google.generativeai, google.cloud, etc.
        "httpx",
        "aiohttp",
        "requests",
        "urllib",  # blocks urllib.request, urllib3 root packages
        "urllib3",
        "pycurl",
        "websockets",
        "websocket",
        "grpc",
    }
)


def _root(module: str) -> str:
    return module.split(".")[0]


def check_file(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"{path}: SyntaxError: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = _root(alias.name)
                if root in _FORBIDDEN_IMPORTS or alias.name in _FORBIDDEN_IMPORTS:
                    violations.append(f"{path}:{node.lineno}: forbidden import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = _root(module)
            if root in _FORBIDDEN_IMPORTS or module in _FORBIDDEN_IMPORTS:
                violations.append(f"{path}:{node.lineno}: forbidden import from '{module}'")
    return violations


def main() -> None:
    embeddings_dir = Path(__file__).resolve().parent.parent.parent / "src" / "stackowl" / "embeddings"
    if not embeddings_dir.exists():
        print("B8 PASS: embeddings/ directory not found (not yet implemented)")
        return

    all_violations: list[str] = []
    files = sorted(embeddings_dir.rglob("*.py"))
    for py_file in files:
        all_violations.extend(check_file(py_file))

    if all_violations:
        for v in all_violations:
            print(f"B8 FAIL: {v}")
        sys.exit(1)

    print(f"B8 PASS: No forbidden network imports in embeddings/ ({len(files)} files scanned)")


if __name__ == "__main__":
    main()
