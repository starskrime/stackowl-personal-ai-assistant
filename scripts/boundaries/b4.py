#!/usr/bin/env python3
"""B4 — the code must run on Linux, macOS and Windows.

That is a STANDING OPERATOR RULE, not a preference. This checker existed to enforce
it and **had never been wired into anything that runs**: it is absent from
`scripts/tripwires.sh`, and its only CI home was a `.pre-commit-config.yaml` hook
that still ran `cd v2 && ...` after the v2->root migration deleted `v2/`. It exited
1 for months and nothing read the verdict.

**WHY IT WAS NEVER WIRED IS THE POINT: it was unwireable.** Measured 2026-09-05, it
reported 23 violations and nearly every one was correct code:

    config_path.py:40   os.path.expandvars(os.path.expanduser(v))  <- pathlib has
                        NO expandvars; this is the portable way to do it
    orchestrator:4409   signal.SIGHUP, inside `if hasattr(signal, "SIGHUP")`
    kill_platform:132   signal.SIGKILL, inside `_terminate_posix`
    skill_validation    r">\\s*/tmp/[^\\s]*\\s*&&\\s*(curl|wget|nc)"  <- a security
                        REGEX that DETECTS /tmp staging
    audit.py:46         "/audit export --output /tmp/audit.json"    <- help text
    docker_argv:115     "--tmpfs", "/tmp:rw,noexec..."             <- a path INSIDE
                        a Linux container, where /tmp is correct by construction

A guard that fires on correct code teaches its reader to ignore it, and that is
exactly what happened. So the rules are now the ones the operator's rule actually
implies, and each carries the carve-out its own findings proved it needs.

**THE `os.path` RULE IS GONE, deliberately.** `os.path` is cross-platform — it was a
STYLE preference (prefer pathlib) smuggled into a PORTABILITY checker, and it
produced 14 of the 23 findings. `ruff`'s `PTH` ruleset does that job properly: it
names the exact replacement per function and, unlike this file, does not flag
`os.path.expandvars`, because there is no pathlib equivalent to suggest. Measured:
23 PTH findings across `src/`. Enabling it is a separate decision, because several
sit inside `shell.py`'s path-safety checks and the ruff baseline may not rise.
"""
import ast
import sys
from pathlib import Path

_POSIX_ONLY_SIGNALS = frozenset({
    "SIGKILL", "SIGUSR1", "SIGUSR2", "SIGHUP", "SIGPIPE",
    "SIGQUIT", "SIGALRM", "SIGCHLD", "SIGCONT", "SIGSTOP",
})

#: A POSIX path literal that is NOT a host path. Keyed on (file, the string itself)
#: so it survives edits above it, and every entry states WHY — an allowlist whose
#: entries carry no reason becomes a place to hide things.
_NOT_A_HOST_PATH: dict[tuple[str, str], str] = {
    ("commands/audit.py", "/audit export --output /tmp/audit.json"):
        "help text — an EXAMPLE invocation shown to the operator, not a path this "
        "code opens.",
    ("tools/knowledge/skill_validation.py", r">\s*/tmp/[^\s]*\s*&&\s*(curl|wget|nc|python)"):
        "a security REGEX that DETECTS a skill staging to /tmp and exfiltrating. "
        "The literal is the threat being matched, not a path being used.",
    ("sandbox/docker_argv.py", "/tmp:rw,noexec,nosuid,nodev,size="):
        "a mount spec INSIDE a Linux container, where /tmp is correct by "
        "construction and the host's temp directory is irrelevant.",
    ("sandbox/mounts.py", "/tmp"):
        "the container-side tmpfs target for bubblewrap, which is Linux-only by "
        "nature (see sandbox/capability.py, which probes and refuses elsewhere).",
}


def _posix_path_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """String constants naming a POSIX temp path — EXCLUDING docstrings.

    A docstring that says "/tmp" is documentation, and both sandbox modules open
    with one. Flagging prose as a hardcoded path is the same category error as
    flagging the security regex that DETECTS /tmp: the literal is being talked
    about, not used.
    """
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "/tmp" in node.value and node.value not in docstrings:
                out.append((node.lineno, node.value))
    return out


def _guarded_signal_lines(tree: ast.AST) -> set[int]:
    """Lines where a POSIX-only signal is legitimately reachable.

    TWO SHAPES, both already used correctly in this tree and both previously
    reported as violations:

      * ``if hasattr(signal, "SIGHUP"):`` — the portable feature test. Everything
        inside that block is guarded.
      * a function whose name says POSIX (``_terminate_posix``) — reached only from
        a platform branch, and the name is the contract.
    """
    safe: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = ast.dump(node.test)
            if "hasattr" in test and "signal" in test:
                for inner in ast.walk(node):
                    if hasattr(inner, "lineno"):
                        safe.add(inner.lineno)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "posix" in node.name.lower() or "unix" in node.name.lower():
                for inner in ast.walk(node):
                    if hasattr(inner, "lineno"):
                        safe.add(inner.lineno)
    return safe


def _check_file(filepath: Path, rel: str) -> list[str]:
    violations: list[str] = []
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return violations

    for lineno, value in _posix_path_literals(tree):
        if (rel, value) in _NOT_A_HOST_PATH:
            continue
        violations.append(
            f"  {rel}:{lineno}: hardcoded /tmp path — use "
            "pathlib.Path(tempfile.gettempdir()). If this literal is NOT a host "
            "path (a regex, help text, or a path inside a container), add it to "
            "_NOT_A_HOST_PATH with that reason."
        )

    safe_lines = _guarded_signal_lines(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _POSIX_ONLY_SIGNALS:
            if node.lineno in safe_lines:
                continue
            violations.append(
                f"  {rel}:{node.lineno}: POSIX-only signal {node.attr!r} reached "
                "without a guard — wrap it in `if hasattr(signal, ...)`, put it in a "
                "function whose name says posix/unix, or branch on sys.platform."
            )

        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    violations.append(
                        f"  {rel}:{node.lineno}: subprocess(..., shell=True) — the "
                        "shell differs per platform (and this is a security seam); "
                        "pass a list of args."
                    )

    return violations


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    src_root = root / "src"
    all_violations: list[str] = []
    scanned = 0
    for py_file in sorted(src_root.rglob("*.py")):
        scanned += 1
        all_violations.extend(
            _check_file(py_file, str(py_file.relative_to(src_root / "stackowl")))
        )

    stale = sorted(
        key for key in _NOT_A_HOST_PATH
        if not (src_root / "stackowl" / key[0]).exists()
    )
    if stale:
        print("B4 FAIL: allowlist entries whose file no longer exists:")
        for key in stale:
            print(f"  {key[0]}")
        sys.exit(1)

    if all_violations:
        print("B4 FAIL: Cross-platform violations detected:")
        for v in all_violations:
            print(v)
        sys.exit(1)

    print(f"B4 PASS: no cross-platform violations ({scanned} files scanned)")


if __name__ == "__main__":
    main()
