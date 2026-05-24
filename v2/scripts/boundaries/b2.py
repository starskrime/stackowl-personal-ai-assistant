#!/usr/bin/env python3
"""B2: No source file in v2/src/ exceeds 300 lines."""
import sys
from pathlib import Path

MAX_LINES = 300


def main() -> None:
    src_root = Path(__file__).resolve().parent.parent.parent / "src"
    violations: list[tuple[Path, int]] = []

    for py_file in src_root.rglob("*.py"):
        line_count = len(py_file.read_text(encoding="utf-8").splitlines())
        if line_count > MAX_LINES:
            violations.append((py_file, line_count))

    if violations:
        print(f"B2 FAIL: {len(violations)} file(s) exceed the {MAX_LINES}-line limit:")
        for path, count in sorted(violations):
            print(f"  {path.relative_to(src_root.parent)} ({count} lines)")
        sys.exit(1)

    scanned = sum(1 for _ in src_root.rglob("*.py"))
    print(f"B2 PASS: All {scanned} source files within {MAX_LINES}-line limit")


if __name__ == "__main__":
    main()
