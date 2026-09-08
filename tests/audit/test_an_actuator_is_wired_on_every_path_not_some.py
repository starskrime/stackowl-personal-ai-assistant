"""A capability passed at one construction site and omitted at another.

WHY THIS EXISTS. `SqliteMemoryBridge.store()` embeds what it writes — it calls
`_embed_content`, and a bridge built without an `embedding_registry` writes the row with a
NULL embedding. The chat path (`memory/assembly.py`) has always passed the registry. The
notifications path (`notifications/assembly.py`) never did, and the method it calls on that
bridge is `store()`: ESC-19's recorder, which puts a delivered proactive message into the
recipient's conversation so the agent has a record of having spoken to them.

So the record existed and was invisible to semantic recall — the actuator wired on only
some paths, which `CLAUDE.md` lists first among the shapes that account for nearly every
real defect here.

THE SAME DEFECT IS RECORDED ONE LAYER DOWN, IN THE CLASS ITSELF. `sqlite_bridge.py`'s
`_embed_content` docstring says the bridge once "HELD AN EMBEDDING REGISTRY AND NEVER USED
IT", and traces the chain: `store()` wrote a StagedFact with no embedding -> staged_facts
was 0% embedded -> `FactReinforcer`'s `WHERE embedding IS NOT NULL` matched nothing ->
the table reached 66% exact duplicates. That was fixed inside the class. This is the second
CONSTRUCTION SITE, and fixing one caller is not fixing the rule.

WHAT THIS ASSERTS is the structural property, not the two sites that exist today: every
construction of a bridge whose writes embed must pass the registry. A third site added
later is caught by the same rule, which is the difference between a fix and a guard.

MEASURED when written: 2 construction sites in `src/`, 1 of which omitted it; staged_facts
held 199 embedded rows against 31 with none.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"

#: Constructors whose writes embed, so a missing registry silently degrades recall.
_MUST_CARRY_REGISTRY = {"SqliteMemoryBridge"}
_REGISTRY_KWARG = "embedding_registry"


def _construction_sites() -> list[tuple[str, int, str, bool]]:
    """(file, line, class, passes the registry) for every construction in src/."""
    found: list[tuple[str, int, str, bool]] = []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — a broken file is another test's problem
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in _MUST_CARRY_REGISTRY:
                continue
            # The class's own `class X(...)` statement is not a call; only real
            # constructions reach here. A site that forwards **kwargs counts as passing.
            passes = any(k.arg == _REGISTRY_KWARG or k.arg is None for k in node.keywords)
            found.append((str(path.relative_to(_ROOT)), node.lineno, name, passes))
    return found


@pytest.mark.tripwire
def test_every_embedding_bridge_is_built_with_its_registry() -> None:
    sites = _construction_sites()
    # THE DENOMINATOR. A rule that finds no construction sites passes silently and
    # forever, which is the failure this repo pays for most.
    assert len(sites) >= 2, f"only {len(sites)} construction site(s) found — rule is blind"
    missing = [f"{f}:{ln} builds {cls} without {_REGISTRY_KWARG}"
               for f, ln, cls, ok in sites if not ok]
    assert not missing, (
        "these write through a method that embeds, so the row lands with a NULL "
        "embedding and is invisible to semantic recall:\n  " + "\n  ".join(missing)
    )


@pytest.mark.tripwire
def test_the_recorder_the_notifier_calls_is_the_one_that_embeds() -> None:
    """The link that makes the omission matter, pinned so it cannot drift apart.

    `notifications/deliverer.py` types its recorder to a one-method Protocol, and that
    method is `store` — the same `store()` that calls `_embed_content`. If the Protocol
    were ever narrowed to a non-embedding method the guard above would be protecting
    nothing, and that would be invisible without this.
    """
    deliverer = (_SRC / "stackowl" / "notifications" / "deliverer.py").read_text(
        encoding="utf-8"
    )
    assert "async def store(self, content: str, session_key: str) -> None: ..." in deliverer
    bridge = (_SRC / "stackowl" / "memory" / "sqlite_bridge.py").read_text(encoding="utf-8")
    assert "_embed_content" in bridge, "the embedding path this guard assumes is gone"
