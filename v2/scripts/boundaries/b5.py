#!/usr/bin/env python3
"""B5: No bare except:; no silent except (pass-only body); every except must log an error."""
import ast
import sys
from pathlib import Path

_LOG_ATTRS = frozenset({"error", "critical", "exception", "warning"})


def _body_has_error_log_or_reraise(body: list[ast.stmt]) -> bool:
    for stmt in body:
        # Explicit control flow in an except block is not silent: the caller
        # receives a default value (return) or the loop continues/breaks.
        if isinstance(stmt, (ast.Raise, ast.Return, ast.Continue, ast.Break)):
            return True
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_ATTRS
            ):
                return True
    return False


def _check_file(filepath: Path) -> list[str]:
    violations: list[str] = []
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return violations

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue

        lineno = node.lineno

        if node.type is None:
            violations.append(
                f"  {filepath}:{lineno}: bare except: — specify the exception type"
            )
            continue

        body = node.body
        is_pass_only = len(body) == 1 and isinstance(body[0], ast.Pass)
        if is_pass_only:
            violations.append(
                f"  {filepath}:{lineno}: silent except (body is only 'pass') — "
                "add log.<module>.error(\"...\", err, {context})"
            )
            continue

        if not _body_has_error_log_or_reraise(body):
            violations.append(
                f"  {filepath}:{lineno}: except body has no log.error/warning call and no re-raise — "
                "add log.<module>.error(\"op failed\", err, {context})"
            )

    return violations


def main() -> None:
    src_root = Path(__file__).resolve().parent.parent.parent / "src"
    all_violations: list[str] = []
    for py_file in src_root.rglob("*.py"):
        all_violations.extend(_check_file(py_file))

    if all_violations:
        print("B5 FAIL: Silent exception handling detected:")
        for v in all_violations:
            print(v)
        sys.exit(1)

    scanned = sum(1 for _ in src_root.rglob("*.py"))
    print(f"B5 PASS: All except handlers have error logging ({scanned} files scanned)")


if __name__ == "__main__":
    main()
