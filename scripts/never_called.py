#!/usr/bin/env python3
"""Find things that exist, work, and are never reached on the path that matters.

SEVEN defects in one session shared this shape, and each presented as something
else entirely — duplicate memories, an empty knowledge graph, prompt pollution, a
silent misroute. Each cost a measurement to find, because the code READS as
correct: the function is right, the column is right, the parameter is right. Only
the effect is missing.

    FactReinforcer            the deduplicator — zero callers
    add_relation              the only writer of RELATED_TO edges — zero callers
    is_machine_lane           "lanes that cannot contain a user fact" — zero callers
    reinforcement_count       a column whose only writer had no callers
    CuratedMemory's target    returned in the payload, never spoken in the message
    embedding_registry        accepted by the bridge, assigned, never used
    embedding_model           supplied by cli/app.py, NOT by memory/assembly.py

TWO SHAPES, AND THE SECOND IS THE DANGEROUS ONE.

  NO CALLER — a symbol defined and never referenced outside its own module. A
  grep finds nothing and the absence is obvious once you look.

  DEFAULT WINS — a keyword parameter with a default that only SOME construction
  sites pass. A grep finds callers. The code reads as wired. The parameter is
  referenced, documented, tested. And the site that actually runs takes the
  default, so the feature is inert in production and nowhere else. This is the
  shape that cost the most this session, and it is the reason this script exists
  rather than a habit of grepping.

Deliberately AST-based, never regex over source text: a grep for a name matches
its own docstring, its comments, and the tests that assert about it — a mistake
already made twice in this codebase's tests.

THE DEFAULT RUN IS THE DEFAULT-WINS REPORT ALONE, narrowed to WIRING modules
(assembly / startup / orchestrator). Unfiltered it is 228 rows, nearly all a
result dataclass built without an optional field, which is normal. Both real
defects had the same structure instead: a collaborator omitted at the site that
assembles the LIVE system while another site passed it. So the question is not
what the parameter is called — that would be a hardcoded word list — it is WHERE
it was dropped.

The no-caller half is OPT-IN and carries a caveat, because in this codebase it is
not trustworthy: tools and handlers are registered dynamically, so a live symbol
can have no static reference. It reports 957 hits and spot checks are false
positives. Every row is a question, not a verdict.

Usage:
    uv run python scripts/never_called.py             # the reliable report
    uv run python scripts/never_called.py --all-sites # every omission (noisy)
    uv run python scripts/never_called.py --no-caller # opt-in, read the caveat
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "stackowl"

#: Dunder and framework hooks are called by machinery, not by name.
_IGNORED_PREFIXES = ("_", "test_")
#: Names whose "caller" is a framework, a protocol, or a CLI decorator.
_FRAMEWORK_NAMES = frozenset({
    "main", "run", "execute", "handle", "check", "health", "close", "start", "stop",
})


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _defined_names(tree: ast.Module) -> set[str]:
    """Top-level functions and classes — the things a caller would name."""
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and (
            not node.name.startswith(_IGNORED_PREFIXES) or node.name.startswith("_")
        ):
            out.add(node.name)
    return out


#: Names appearing inside a STRING annotation. With `from __future__ import
#: annotations` — which this codebase uses everywhere — every type reference is a
#: string literal, so an AST walk over Name nodes misses all of them. The first
#: version of this script did exactly that and reported 1,045 dead symbols,
#: almost all of them schema classes referenced only in annotations. A sweep that
#: cries wolf 1,045 times is worse than no sweep.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _referenced_names(tree: ast.Module) -> set[str]:
    """Every name this module MENTIONS — calls, imports, attributes, annotations."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name.split(".")[-1])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Annotation strings, TypeVar bounds, cast() targets, Literal members.
            # Over-collecting here is SAFE: it can only suppress a false "dead"
            # report, never invent one.
            out.update(_IDENT_RE.findall(node.value))
    return out


def find_no_caller() -> list[tuple[str, str]]:
    """Symbols defined in one module and referenced by no other."""
    defined: dict[str, list[str]] = defaultdict(list)
    referenced: dict[str, set[str]] = {}
    for path in _modules():
        tree = _parse(path)
        if tree is None:
            continue
        rel = str(path.relative_to(SRC))
        for name in _defined_names(tree):
            defined[name].append(rel)
        referenced[rel] = _referenced_names(tree)

    out: list[tuple[str, str]] = []
    for name, homes in sorted(defined.items()):
        if name in _FRAMEWORK_NAMES or name.startswith("__"):
            continue
        elsewhere = any(
            name in names for mod, names in referenced.items() if mod not in homes
        )
        if not elsewhere:
            out.append((name, homes[0]))
    return out


def find_default_wins(*, wiring_only: bool = True) -> list[tuple[str, str, str, list[str]]]:
    """Keyword params with defaults that SOME construction sites omit.

    Reported as (callee, param, definition site, list of omitting call sites).
    Only reported when at least one caller DOES pass it — that asymmetry is the
    signal. A parameter nobody ever passes is a design question; a parameter one
    caller passes and another does not is usually a bug in the one that does not.
    """
    # callee -> {param names with defaults}
    defaults: dict[str, set[str]] = {}
    def_site: dict[str, str] = {}
    for path in _modules():
        tree = _parse(path)
        if tree is None:
            continue
        rel = str(path.relative_to(SRC))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef) and sub.name == "__init__":
                        names = _kwdefaults(sub)
                        if names:
                            defaults[node.name] = names
                            def_site[node.name] = f"{rel}:{node.lineno}"
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names = _kwdefaults(node)
                if names:
                    defaults.setdefault(node.name, names)
                    def_site.setdefault(node.name, f"{rel}:{node.lineno}")

    # callee -> param -> (passed_at, omitted_at)
    passed: dict[tuple[str, str], list[str]] = defaultdict(list)
    omitted: dict[tuple[str, str], list[str]] = defaultdict(list)
    for path in _modules():
        tree = _parse(path)
        if tree is None:
            continue
        rel = str(path.relative_to(SRC))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if callee not in defaults:
                continue
            given = {kw.arg for kw in node.keywords if kw.arg}
            for param in defaults[callee]:
                site = f"{rel}:{node.lineno}"
                if param in given:
                    passed[(callee, param)].append(site)
                else:
                    omitted[(callee, param)].append(site)

    out: list[tuple[str, str, str, list[str]]] = []
    for key, sites in sorted(passed.items()):
        callee, param = key
        misses = omitted.get(key, [])
        if not (sites and misses):
            continue
        if wiring_only:
            misses = [m for m in misses if _is_wiring(m)]
            if not misses:
                continue
        out.append((callee, param, def_site.get(callee, "?"), misses))
    return out


#: Modules that WIRE the running system together. The narrowing is structural,
#: not lexical — no guessing from parameter names, which would be a hardcoded
#: word list and is a standing rule against.
#:
#: WHY THIS IS THE RIGHT FILTER. Unfiltered, the report is 228 rows, almost all
#: of them a result dataclass built without an optional field — which is normal
#: and not a defect. Both REAL defects had the same structure instead: a
#: collaborator omitted at the site that assembles the LIVE system, while another
#: site passed it. `SqliteLessonsStore(db)` in memory/assembly.py against
#: `SqliteLessonsStore(db, embedding_model=...)` in cli/app.py is the exact shape.
#: So the question is not what the parameter is called, it is WHERE it was
#: dropped.
_WIRING_MARKERS = ("assembly", "startup/", "orchestrator")


def _is_wiring(site: str) -> bool:
    return any(marker in site for marker in _WIRING_MARKERS)


def _kwdefaults(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    args = fn.args
    for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        if d is not None:
            names.add(a.arg)
    positional = args.posonlyargs + args.args
    if args.defaults:
        for a in positional[-len(args.defaults):]:
            names.add(a.arg)
    names.discard("self")
    names.discard("cls")
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--defaults", action="store_true", help="only the default-wins report")
    ap.add_argument("--no-caller", action="store_true", help="only the no-caller report")
    ap.add_argument("--all-sites", action="store_true",
                    help="do not restrict default-wins to wiring modules (noisy)")
    ns = ap.parse_args()
    # The DEFAULT run is the default-wins report ALONE. The no-caller half is
    # opt-in, because in this codebase it is not trustworthy — see its caveat.
    if ns.no_caller:
        rows = find_no_caller()
        print(f"=== NO CALLER — defined, never referenced outside src/ ({len(rows)}) ===")
        print("    CAVEAT, READ BEFORE ACTING: this codebase registers tools and")
        print("    handlers DYNAMICALLY, so a live symbol can have no static")
        print("    reference. Measured: 957 hits, and spot checks are false")
        print("    positives. Treat every row as a QUESTION, never a verdict. It is")
        print("    opt-in for that reason — a report that cries wolf 957 times")
        print("    teaches its reader to ignore it, which is worse than no report.")
        for name, home in rows:
            print(f"  {name:44s} {home}")
        print()

    if not ns.no_caller or ns.defaults:
        rows2 = find_default_wins(wiring_only=not ns.all_sites)
        scope = "ALL sites" if ns.all_sites else "WIRING sites only (assembly/startup/orchestrator)"
        print(f"=== DEFAULT WINS — some callers pass it, the wiring does not ({len(rows2)}) ===")
        print(f"    scope: {scope}")
        print("    (the dangerous shape: the symbol IS referenced, so the code reads as wired)")
        for callee, param, site, misses in rows2:
            print(f"  {callee}.{param}   defined {site}")
            for m in misses:
                print(f"      omitted at {m}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
