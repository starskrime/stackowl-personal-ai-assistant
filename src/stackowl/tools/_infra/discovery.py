"""Tool auto-discovery — find Tool subclasses without importing the whole tree (D05.1).

Replaces ~61 hand-written imports in ``ToolRegistry.with_defaults()``, so a new
tool is registered by existing rather than by remembering to add a line.

HOW, and why the AST step is not optional. ``tools/`` holds 130 modules and only
61 define a tool; the rest are schemas, path guards, consent helpers, ``_infra``.
Importing all 130 to ask each one "are you a tool?" would execute 69 modules for
nothing and make every import-time side effect in the tree a boot cost. So:

    glob tools/**/*.py
      → AST: does this module define a TOP-LEVEL class inheriting Tool?
      → import only those
      → issubclass(obj, Tool) is the REAL check; the AST is only a filter

PORTED DESIGN, NOT CODE. The reference platform's predicate looks for a top-level
``registry.register(...)`` CALL, because its tools self-register at import. Ours
are ``Tool`` SUBCLASSES that the registry instantiates, so their predicate would
match nothing here. Same technique, different target.

WHY THIS DOES NOT INSTANTIATE ANYTHING. ``with_defaults()`` does not merely
construct tools — it SHARES dependencies: one ``UndoStore`` across
edit/apply_patch/undo_write, one ``PlanStore`` across todo/update_plan. Every one
of those constructors accepts ``store=None`` and quietly builds its own, so a
discovery that called ``cls()`` on everything would register 77 working tools and
leave ``undo_write`` unable to undo anything ``edit`` did — green tests, silent
breakage. Discovery therefore yields CLASSES and the caller owns construction;
:func:`requires_explicit_wiring` is what makes forgetting that loud.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.tools.base import Tool

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["EXCLUDED_FROM_DISCOVERY", "discover_tool_classes", "requires_explicit_wiring"]

#: Package root for the import path this module reconstructs from file paths.
_PACKAGE_ROOT = "stackowl.tools"

#: Concrete Tool subclasses that must NOT be auto-registered.
#:
#: ``LearnedShellTool`` is one class that becomes MANY tools: ``LearnedToolLoader``
#: constructs one instance per agent-authored JSON spec, each with its own name.
#: Auto-registering the class itself would add a nameless template tool alongside
#: the real ones. This is the only case so far, and it is listed rather than
#: inferred because "has a required constructor arg" would be a coincidence, not
#: a reason.
EXCLUDED_FROM_DISCOVERY: frozenset[str] = frozenset({"LearnedShellTool"})


def _defines_a_tool_subclass(source: str) -> bool:
    """Whether a module's SOURCE declares a top-level class inheriting ``Tool``.

    Syntactic and deliberately shallow — it cannot resolve inheritance across
    modules and does not try. Indirect subclasses (the 18 browser tools deriving
    from ``_BrowserTool``) are still found, because ``_BrowserTool(Tool)`` is
    itself top-level in that same file, so the file passes and ``issubclass``
    picks up everything in it.

    A cheap substring check first: a module that never mentions ``Tool`` cannot
    declare a subclass of it, and skipping ``ast.parse`` for those is most of the
    saving.
    """
    if "Tool" not in source:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.ClassDef)
        and any(
            (isinstance(b, ast.Name) and b.id == "Tool")
            or (isinstance(b, ast.Attribute) and b.attr == "Tool")
            for b in node.bases
        )
        for node in tree.body
    )


def _candidate_modules(root: Path) -> Iterator[str]:
    """Yield dotted module names for files that pass the AST filter."""
    for path in sorted(root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as err:
            log.tool.warning(
                "[discovery] unreadable module — skipped",
                extra={"_fields": {"path": str(path), "err": str(err)}},
            )
            continue
        if not _defines_a_tool_subclass(source):
            continue
        rel = path.relative_to(root).with_suffix("")
        yield f"{_PACKAGE_ROOT}." + ".".join(rel.parts)


def requires_explicit_wiring(tool_cls: type[Tool]) -> bool:
    """Whether ``tool_cls`` must be constructed by hand rather than as ``cls()``.

    True when ``__init__`` declares any parameter at all — even one with a
    default. That is deliberately stricter than "has a REQUIRED parameter",
    because the bug this prevents is precisely a defaulted one:
    ``EditTool(store=None)`` constructs perfectly well and silently gets its own
    ``UndoStore`` instead of the shared one.

    The caller treats a True here as "must be in the wiring table", so adding a
    shared-dependency tool and forgetting to wire it fails loudly at boot rather
    than quietly severing undo.
    """
    try:
        params = list(inspect.signature(tool_cls.__init__).parameters.values())[1:]
    except (TypeError, ValueError):  # pragma: no cover — builtins/slots oddities
        return True  # cannot introspect → demand explicit wiring
    return any(
        p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params
    )


def discover_tool_classes(root: Path | None = None) -> list[type[Tool]]:
    """Return every concrete ``Tool`` subclass under ``tools/``, sorted by name.

    Sorted so registration order is deterministic — the presented set is ordered
    downstream by :class:`ToolPresentation`, but a stable order here keeps boot
    logs and any order-sensitive test reproducible.

    Never raises on one bad module: an import failure is logged and skipped, so a
    single broken tool file cannot wedge boot (mirrors ``LearnedToolLoader``).
    """
    root = root or Path(__file__).resolve().parent.parent
    # 1. ENTRY
    log.tool.debug("[discovery] discover_tool_classes: entry",
                   extra={"_fields": {"root": str(root)}})

    found: dict[str, type[Tool]] = {}
    scanned = 0
    for module_name in _candidate_modules(root):
        scanned += 1
        try:
            module = importlib.import_module(module_name)
        except Exception as err:  # noqa: BLE001 — one bad file must not wedge boot
            log.tool.error(
                "[discovery] tool module failed to import — skipped",
                exc_info=err, extra={"_fields": {"module": module_name}},
            )
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, Tool) or obj is Tool or inspect.isabstract(obj):
                continue
            if obj.__name__ in EXCLUDED_FROM_DISCOVERY:
                continue
            # getmembers also returns classes IMPORTED into the module, so the
            # same class is seen repeatedly across modules. Keyed by qualified
            # name, which dedupes without depending on scan order.
            found[f"{obj.__module__}.{obj.__qualname__}"] = obj

    classes = [found[k] for k in sorted(found)]
    # 4. EXIT
    log.tool.debug(
        "[discovery] discover_tool_classes: exit",
        extra={"_fields": {"modules_imported": scanned, "classes": len(classes)}},
    )
    return classes
