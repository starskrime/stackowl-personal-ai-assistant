"""The bridge held an embedding registry and never used it.

MEASURED 2026-08-25, and it is a three-layer chain from one unused line:

    SqliteMemoryBridge.__init__ takes `embedding_registry`, assigns it to
    `self._embeddings` — and the only other reference in the entire file is the
    constructor's own log field.

  -> `store()` builds a StagedFact with no embedding
  -> staged_facts is 0% embedded (0 of 5,212 before the wipe, 0 of 16 after)
  -> `FactReinforcer`'s query is `WHERE ... embedding IS NOT NULL`, so it matches
     nothing, on every run, forever
  -> the table reached 66% exact duplicates — 3,462 rows in 50 families

Bakir's complaint was "it just adds, adds, adds ... there is no similarity
check". There WAS one. It could not see anything, because the vectors it needed
were never written.

This is the SIXTH thing found in this session that was built for exactly its
stated purpose and never wired to a caller — after FactReinforcer itself,
`add_relation` (the RELATED_TO graph edges), `is_machine_lane`,
`reinforcement_count`, and the `target` CuratedMemory returned but no message
ever spoke.

THE MODEL NAME IS RECORDED TOO, and that is not incidental. The dedup gate
refuses to compare two vectors unless their `embedding_model` matches and is
non-empty — because lessons carry '' on all 5,146 rows and reflections mix
all-MiniLM-L6-v2 with the degraded `hash-v1-384d` fallback, both 384-dim, so the
arithmetic succeeds and the answer is meaningless. Writing a vector without its
model would produce rows the gate must then refuse — the defect one step along.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from stackowl.memory.sqlite_bridge import SqliteMemoryBridge


def _awaited_attrs(fn: Any) -> set[str]:
    """Attribute names actually CALLED in `fn` — comments and strings excluded."""
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


class _StubProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.1] * 384 for _ in texts]


class _StubRegistry:
    """Mirrors EmbeddingRegistry's real shape: .get() -> provider, .active_model."""

    def __init__(self) -> None:
        self.provider = _StubProvider()

    def get(self) -> _StubProvider:
        return self.provider

    @property
    def active_model(self) -> str:
        return "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# The unused parameter
# ---------------------------------------------------------------------------

def test_stage_actually_uses_the_embedding_registry() -> None:
    """The defect, asserted on the CALLS rather than the source text — grepping
    for a name matches this test's own explanatory comments.

    IT ASSERTS `stage()`, NOT `store()`, and the move was measured rather than
    tidied. The embedding started in store(), which is ONE of four writers into
    staged_facts; pellet_generator, rollover_summary and incident_escalation all
    call stage() directly. A fact written AFTER that fix went live still had no
    vector — source_ref "outcome:shell:stop", straight from the incident path —
    which is this codebase's first failure mode: an actuator wired on some paths.
    stage() is the single INSERT and the only place all four converge.
    """
    called = _awaited_attrs(SqliteMemoryBridge.stage)
    assert "_embedded" in called, (
        "stage() must embed — it is the ONE insert every writer reaches, and the "
        "registry the bridge was handed sat unused, which is why staged_facts was "
        "0% embedded and FactReinforcer could never match a row"
    )


# ---------------------------------------------------------------------------
# The behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_stored_fact_carries_a_vector_and_its_model(tmp_db: Any) -> None:
    registry = _StubRegistry()
    bridge = SqliteMemoryBridge(tmp_db, embedding_registry=registry)  # type: ignore[arg-type]

    await bridge.store("Bakir prefers root-cause fixes.", "72055773")

    row = (await tmp_db.fetch_all(
        "SELECT embedding, embedding_model FROM staged_facts LIMIT 1"
    ))[0]
    assert row["embedding"] is not None, "the vector must reach the row"
    assert row["embedding_model"] == "all-MiniLM-L6-v2", (
        "a vector without its model is a row the dedup gate must refuse to "
        "compare — the same defect one step along"
    )
    assert registry.provider.calls == [["Bakir prefers root-cause fixes."]]


@pytest.mark.asyncio
async def test_no_registry_still_stores_the_fact(tmp_db: Any) -> None:
    """Embedding is an enhancement, never a gate on remembering. A bridge built
    without a registry must still write the fact."""
    bridge = SqliteMemoryBridge(tmp_db, embedding_registry=None)

    await bridge.store("something worth keeping", "72055773")

    rows = await tmp_db.fetch_all("SELECT content, embedding FROM staged_facts")
    assert len(rows) == 1
    assert rows[0]["embedding"] is None


@pytest.mark.asyncio
async def test_an_embedding_failure_never_costs_the_write(tmp_db: Any) -> None:
    """B5. The fact is the point; the vector is an optimisation for later recall.
    A provider that raises must not lose what the user just said."""

    class _Boom:
        def get(self) -> Any:
            raise RuntimeError("provider down")

        @property
        def active_model(self) -> str:
            return "x"

    bridge = SqliteMemoryBridge(tmp_db, embedding_registry=_Boom())  # type: ignore[arg-type]

    await bridge.store("do not lose me", "72055773")

    rows = await tmp_db.fetch_all("SELECT content, embedding FROM staged_facts")
    assert len(rows) == 1, "the fact must survive an embedding failure"
    assert rows[0]["content"] == "do not lose me"
    assert rows[0]["embedding"] is None
