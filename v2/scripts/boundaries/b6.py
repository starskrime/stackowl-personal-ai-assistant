#!/usr/bin/env python3
"""B6: mypy --strict must pass on the entire v2/src/stackowl/ tree."""
import subprocess
import sys
from pathlib import Path


def main() -> None:
    src_root = Path(__file__).resolve().parent.parent.parent / "src" / "stackowl"
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(src_root)],
    )
    if result.returncode != 0:
        print("B6 FAIL: mypy --strict reported errors (see output above)")
        sys.exit(1)
    print("B6 PASS: mypy --strict clean")


if __name__ == "__main__":
    main()
