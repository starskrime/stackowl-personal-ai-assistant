#!/usr/bin/env python3
"""B9: No blocking I/O patterns in JobHandler.execute() implementations."""

from __future__ import annotations

import sys
from pathlib import Path

HANDLERS_DIRS = [
    Path("src/stackowl/scheduler/handlers"),
    Path("src/stackowl/memory"),
]
BLOCKED_PATTERNS = ["time.sleep(", "requests.", "urllib.request."]


def check_file(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        # B5 — never silent
        print(f"B9 WARN: could not read {path}: {exc}", file=sys.stderr)
        return violations
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pattern in BLOCKED_PATTERNS:
            if pattern in line:
                violations.append(f"{path}:{line_no}: '{pattern}' — use async equivalent")
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    all_violations: list[str] = []
    for rel_dir in HANDLERS_DIRS:
        full = repo_root / rel_dir
        if not full.exists():
            continue
        for py_file in full.rglob("*.py"):
            all_violations.extend(check_file(py_file))
    if all_violations:
        print(f"B9 FAIL: {len(all_violations)} blocking I/O violation(s):")
        for v in all_violations:
            print(f"  {v}")
        return 1
    print("B9 PASS: No blocking I/O patterns detected in handler files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
