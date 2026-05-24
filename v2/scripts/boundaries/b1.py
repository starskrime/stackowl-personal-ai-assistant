#!/usr/bin/env python3
"""B1: No circular imports across stackowl.* packages (AST-based DAG cycle check)."""
import ast
import sys
from collections import defaultdict
from pathlib import Path


def _file_to_module(filepath: Path, src_root: Path) -> str:
    rel = filepath.relative_to(src_root)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _get_local_imports(filepath: Path, src_root: Path) -> list[str]:
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("stackowl."):
                    imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("stackowl."):
                imports.append(node.module)
            elif node.level and node.level > 0:
                parts = filepath.relative_to(src_root / "stackowl").with_suffix("").parts
                parent_parts = list(parts)[: -node.level]
                base = "stackowl." + ".".join(parent_parts) if parent_parts else "stackowl"
                target = f"{base}.{node.module}" if node.module else base
                imports.append(target)
    return imports


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    visited: set[str] = set()
    in_stack: set[str] = set()
    path: list[str] = []
    cycles: list[list[str]] = []

    def dfs(node: str) -> None:
        visited.add(node)
        in_stack.add(node)
        path.append(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in in_stack:
                start = path.index(neighbor)
                cycles.append(path[start:] + [neighbor])
        path.pop()
        in_stack.discard(node)

    for node in sorted(graph):
        if node not in visited:
            dfs(node)
    return cycles


def main() -> None:
    src_root = Path(__file__).resolve().parent.parent.parent / "src"
    stackowl_root = src_root / "stackowl"
    if not stackowl_root.exists():
        print(f"B1: {stackowl_root} not found — nothing to scan")
        sys.exit(0)

    graph: dict[str, set[str]] = defaultdict(set)
    for py_file in stackowl_root.rglob("*.py"):
        module = _file_to_module(py_file, src_root)
        for imp in _get_local_imports(py_file, src_root):
            if imp.startswith("stackowl.") and imp != module:
                graph[module].add(imp)

    cycles = _find_cycles(dict(graph))
    if cycles:
        print("B1 FAIL: Circular imports detected:")
        for cycle in cycles:
            print("  " + " -> ".join(cycle))
        sys.exit(1)

    scanned = sum(1 for _ in stackowl_root.rglob("*.py"))
    print(f"B1 PASS: No circular imports ({scanned} modules scanned)")


if __name__ == "__main__":
    main()
