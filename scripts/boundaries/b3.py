#!/usr/bin/env python3
"""B3: No hardcoded English stopword collections; no ASCII-only regex without UNICODE flag."""
import ast
import re
import sys
from pathlib import Path

_ENGLISH_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "of", "in", "on", "at",
    "by", "for", "with", "about", "as", "into", "through", "from",
})

_ASCII_CHAR_CLASS = re.compile(r"\[[a-zA-Z]{2,}(?:-[a-zA-Z])?\]")
_RE_CALL_ATTRS = frozenset({"compile", "match", "search", "fullmatch", "findall", "sub", "split"})


def _is_re_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _RE_CALL_ATTRS
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
    )


def _has_unicode_flag(node: ast.Call) -> bool:
    for arg in node.args[1:]:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Attribute) and sub.attr in ("UNICODE", "U"):
                return True
    for kw in node.keywords:
        for sub in ast.walk(kw.value):
            if isinstance(sub, ast.Attribute) and sub.attr in ("UNICODE", "U"):
                return True
    return False


def _check_file(filepath: Path) -> list[str]:
    violations: list[str] = []
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return violations

    for node in ast.walk(tree):
        # Hardcoded English stopword collections (set/list/tuple with 3+ stopwords)
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            found = [
                e.value
                for e in node.elts
                if isinstance(e, ast.Constant)
                and isinstance(e.value, str)
                and e.value.strip().lower() in _ENGLISH_STOPWORDS
            ]
            if len(found) >= 3:
                violations.append(
                    f"  {filepath}:{node.lineno}: hardcoded English stopword collection "
                    f"({len(found)} stopwords found: {found[:3]}...)"
                )

        # ASCII-only regex without UNICODE flag
        if isinstance(node, ast.Call) and _is_re_call(node) and node.args:
            pattern_node = node.args[0]
            if (
                isinstance(pattern_node, ast.Constant)
                and isinstance(pattern_node.value, str)
                and _ASCII_CHAR_CLASS.search(pattern_node.value)
                and not _has_unicode_flag(node)
            ):
                violations.append(
                    f"  {filepath}:{pattern_node.lineno}: ASCII-only regex char class "
                    f"without re.UNICODE: {pattern_node.value!r}"
                )

    return violations


def main() -> None:
    src_root = Path(__file__).resolve().parent.parent.parent / "src"
    all_violations: list[str] = []
    for py_file in src_root.rglob("*.py"):
        all_violations.extend(_check_file(py_file))

    if all_violations:
        print("B3 FAIL: Hardcoded English patterns or ASCII-only regex detected:")
        for v in all_violations:
            print(v)
        sys.exit(1)

    scanned = sum(1 for _ in src_root.rglob("*.py"))
    print(f"B3 PASS: No hardcoded English or ASCII regex patterns ({scanned} files scanned)")


if __name__ == "__main__":
    main()
