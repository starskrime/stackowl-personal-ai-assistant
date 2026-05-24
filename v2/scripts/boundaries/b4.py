#!/usr/bin/env python3
"""B4: No /tmp literals; no os.path usage; no subprocess shell=True; no POSIX-only signals."""
import ast
import sys
from pathlib import Path

_POSIX_ONLY_SIGNALS = frozenset({
    "SIGKILL", "SIGUSR1", "SIGUSR2", "SIGHUP", "SIGPIPE",
    "SIGQUIT", "SIGALRM", "SIGCHLD", "SIGCONT", "SIGSTOP",
})


def _is_os_path(node: ast.Attribute) -> bool:
    return (
        isinstance(node.value, ast.Attribute)
        and node.value.attr == "path"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "os"
    )


def _check_file(filepath: Path) -> list[str]:
    violations: list[str] = []
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return violations

    for node in ast.walk(tree):
        # /tmp string literals
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "/tmp" in node.value:
                violations.append(
                    f"  {filepath}:{node.lineno}: hardcoded /tmp path — "
                    "use platformdirs or pathlib.Path(tempfile.gettempdir())"
                )

        # os.path.* usage
        if isinstance(node, ast.Attribute) and _is_os_path(node):
            violations.append(
                f"  {filepath}:{node.lineno}: os.path usage — use pathlib.Path"
            )

        # subprocess shell=True
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    violations.append(
                        f"  {filepath}:{node.lineno}: subprocess(..., shell=True) — "
                        "use a list of args instead"
                    )

        # POSIX-only signal names
        if isinstance(node, ast.Attribute) and node.attr in _POSIX_ONLY_SIGNALS:
            violations.append(
                f"  {filepath}:{node.lineno}: POSIX-only signal {node.attr!r} — "
                "use signal.SIGTERM / signal.SIGINT or branch on platform.system()"
            )

    return violations


def main() -> None:
    src_root = Path(__file__).resolve().parent.parent.parent / "src"
    all_violations: list[str] = []
    for py_file in src_root.rglob("*.py"):
        all_violations.extend(_check_file(py_file))

    if all_violations:
        print("B4 FAIL: Cross-platform violations detected:")
        for v in all_violations:
            print(v)
        sys.exit(1)

    scanned = sum(1 for _ in src_root.rglob("*.py"))
    print(f"B4 PASS: No cross-platform violations ({scanned} files scanned)")


if __name__ == "__main__":
    main()
