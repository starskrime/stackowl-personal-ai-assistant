# Persona-Evolution Feedback Loop + Memory Gaps — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make evolved owl DNA actually reach the live persona — safely (slow-poison governor), live (next-turn boundary), and persistently (boot hydration) — and close two small memory gaps.

**Architecture:** Today `EvolutionCoordinator` writes evolved DNA to the `owl_dna` table but nothing reads it back, so the persona always uses boot-default DNA. 4a adds a `_bound_dna` safety governor (max-delta + envelope + floors), a shared `apply_dna_overlay` primitive (`model_copy(dna)` → `registry.replace`), a fail-safe boot `DnaHydrator`, and wires `EvolutionCoordinator` to clamp→persist→refresh→audit. 4b makes `FactPromoter` compute a missing embedding so promoted facts are semantically recallable, proven by a cross-session journey.

**Tech Stack:** Python 3.11+, Pydantic v2 (frozen, already `ge=0,le=1`-clamped DNA), SQLite, asyncio, pytest, ruff, mypy --strict. Code under `v2/`. Tests: `uv run pytest <path> -v` (NO `--timeout`; targeted paths only — full suite hangs).

---

## ⚠️ Reuse Ledger — NO DUPLICATE CODE (read first)

Operator's standing complaint: ~50% of written code is duplicated. **Every task extends existing seams.** Each implementer MUST grep for the existing impl first and report reused-vs-created. Pre-made decisions:

| Concern | Decision | Single source of truth |
|---|---|---|
| [0,1] clamp | **REUSE** — `OwlDNA` fields are already `Field(ge=0,le=1)` + `mutate()` clamps. `_bound_dna` does NOT re-clamp [0,1]; it adds max-delta + envelope + floors only. | `owls/dna.py` |
| DNA overlay (model_copy(dna)→replace) | **CREATE one** free function `apply_dna_overlay(registry, name, dna)`; reused by the boot hydrator AND EvolutionCoordinator. No event bus, no Protocol class (one impl). | `owls/dna_hydrator.py` |
| owl_dna read | **EXTEND** — lift `owls_command._SELECT_DNA_SQL` into a shared read-all helper; the hydrator + the command use the one query. | one helper |
| Envelope anchor | **REUSE the neutral default** — every authored DNA is `OwlDNA()` (0.5); anchor the envelope on `0.5 ± ENVELOPE` (a constant), no `DnaDefaults` plumbing. (Authored-non-neutral DNA → Phase-2.) | `owls/evolution_limits.py` |
| Registry refresh | **REUSE** `OwlRegistry.replace` (owl-builder S1 atomic swap). | `owls/registry.py` |
| Embedding | **REUSE** `commands/memory_helpers._best_effort_embed` (async, returns `(vec\|None, model\|None)`, fail-open) in the promoter — do NOT reimplement. | `commands/memory_helpers.py` |
| Mutable traits | **REUSE** `owls/dna._MUTABLE_TRAITS` (the 6 governed traits). | `owls/dna.py` |
| Limits/constants | **EXTEND the precedent** — a constants module `owls/evolution_limits.py` (mirrors `owls/delegation_limits.py`). No settings plumbing. | `owls/evolution_limits.py` |

---

## Decomposition: 4a (T1–T4) then 4b (T5–T6). 4a = owls-package feedback loop; 4b = memory-package gaps.

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/owls/evolution_limits.py` | **Create** | `MAX_DELTA`, `ENVELOPE`, `DNA_NEUTRAL`, `FLOOR_TRAITS`, `TRAIT_FLOOR` |
| `src/stackowl/owls/dna_governor.py` | **Create** | `bound_dna(current, proposed) -> OwlDNA` (max-delta + envelope + floors) |
| `src/stackowl/owls/dna_hydrator.py` | **Create** | `apply_dna_overlay(...)`, `read_all_owl_dna(db)`, `_coerce_dna(...)`, `hydrate_dna(registry, db)` |
| `src/stackowl/commands/owls_command.py` | Modify | use the shared `read_all_owl_dna`/SELECT (DRY) |
| `src/stackowl/startup/orchestrator.py` | Modify | call `hydrate_dna` after personas + db open |
| `src/stackowl/owls/evolution.py` | Modify | `bound_dna` before persist; `apply_dna_overlay` after persist; audit-log |
| `src/stackowl/memory/fact_promoter.py` | Modify | `embedding_registry` param; embed in `_promote_one` when missing |
| `src/stackowl/memory/assembly.py` + `src/stackowl/tools/knowledge/memory.py` | Modify | pass `embedding_registry` (+`lancedb`) to `FactPromoter` |
| tests (per task) | **Create** | units + 3 journeys |

---

## STORY 4a — DNA-evolution → persona feedback (safe, live, persistent)

### Task 1: `_bound_dna` safety governor + limits

**Files:** Create `src/stackowl/owls/evolution_limits.py`, `src/stackowl/owls/dna_governor.py`; Test `tests/owls/test_dna_governor.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_dna_governor.py
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_governor import bound_dna


def test_max_delta_caps_a_big_move():
    cur = OwlDNA(curiosity=0.50)
    proposed = OwlDNA(curiosity=0.95)            # +0.45 proposed
    out = bound_dna(cur, proposed)
    assert abs(out.curiosity - 0.55) < 1e-9       # capped to +MAX_DELTA (0.05)


def test_envelope_clamps_to_neutral_band():
    # drive curiosity up over many "batches" — envelope caps at 0.5+ENVELOPE
    cur = OwlDNA(curiosity=0.69)
    proposed = OwlDNA(curiosity=0.74)            # +0.05 ok by delta, but 0.74 > 0.70 envelope
    out = bound_dna(cur, proposed)
    assert out.curiosity <= 0.70 + 1e-9           # ENVELOPE=0.2 around 0.5


def test_safety_floor_on_judgment_traits():
    cur = OwlDNA(challenge_level=0.30)
    proposed = OwlDNA(challenge_level=0.26)       # would dip toward the <0.3 "no pushback" zone
    out = bound_dna(cur, proposed)
    assert out.challenge_level >= 0.25            # floor holds
    cur2 = OwlDNA(precision=0.28)
    assert bound_dna(cur2, OwlDNA(precision=0.20)).precision >= 0.25


def test_no_change_is_identity():
    cur = OwlDNA(verbosity=0.5)
    assert bound_dna(cur, OwlDNA(verbosity=0.5)).verbosity == 0.5


def test_decay_rate_field_untouched():
    cur = OwlDNA(decay_rate_per_week=0.05)
    # decay_rate is not a mutable trait — passes through unchanged
    assert bound_dna(cur, OwlDNA(decay_rate_per_week=0.9)).decay_rate_per_week == 0.05
```

- [ ] **Step 2: Run — verify FAIL.** `cd v2 && uv run pytest tests/owls/test_dna_governor.py -v`

- [ ] **Step 3: Implement**

`owls/evolution_limits.py`:
```python
"""Safety limits for DNA evolution (mirrors owls/delegation_limits.py).

The DNA-evolution feedback loop is a positive-feedback control system; these
bound it so conversation-driven evolution cannot slow-poison the persona.
"""
DNA_NEUTRAL = 0.5            # every authored owl DNA defaults to neutral 0.5
MAX_DELTA = 0.05            # max move per trait per evolution batch (rate cap)
ENVELOPE = 0.2             # evolution orbits DNA_NEUTRAL ± ENVELOPE (range cap)
TRAIT_FLOOR = 0.25         # judgment traits may never drop below this
FLOOR_TRAITS = frozenset({"challenge_level", "precision"})  # willingness to push back / be precise
```

`owls/dna_governor.py`:
```python
"""bound_dna — the single DNA-safety governor for evolution (clamp/cap/envelope/floor)."""
from __future__ import annotations

from stackowl.owls.dna import _MUTABLE_TRAITS, OwlDNA
from stackowl.owls.evolution_limits import (
    DNA_NEUTRAL, ENVELOPE, FLOOR_TRAITS, MAX_DELTA, TRAIT_FLOOR,
)


def bound_dna(current: OwlDNA, proposed: OwlDNA) -> OwlDNA:
    """Return a safe DNA: per mutable trait, cap the move to ±MAX_DELTA, clamp into
    DNA_NEUTRAL±ENVELOPE, and hold the floor on judgment traits. OwlDNA already
    enforces [0,1] on construction (Field ge/le), so this adds rate+range+floor only."""
    updates: dict[str, float] = {}
    for trait in _MUTABLE_TRAITS:
        cur = float(getattr(current, trait))
        prop = float(getattr(proposed, trait))
        delta = max(-MAX_DELTA, min(MAX_DELTA, prop - cur))   # rate cap
        moved = cur + delta
        lo, hi = DNA_NEUTRAL - ENVELOPE, DNA_NEUTRAL + ENVELOPE
        moved = max(lo, min(hi, moved))                        # envelope (range cap)
        if trait in FLOOR_TRAITS:
            moved = max(TRAIT_FLOOR, moved)                    # safety floor
        updates[trait] = moved
    return current.model_copy(update=updates)
```
(`_MUTABLE_TRAITS` is the 6 traits; `decay_rate_per_week` is excluded → untouched. `model_copy(update=...)` on the frozen model is fine; OwlDNA's `ge/le` are not re-validated by model_copy, but our values are already in-range by construction.)

- [ ] **Step 4: Run — verify PASS** (5).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/owls/evolution_limits.py v2/src/stackowl/owls/dna_governor.py v2/tests/owls/test_dna_governor.py
git commit -m "feat(v2): bound_dna safety governor (max-delta+envelope+floors) (persona-evo T1)"
```

---

### Task 2: `apply_dna_overlay` + `DnaHydrator` (boot persistence)

**Files:** Create `src/stackowl/owls/dna_hydrator.py`; Modify `src/stackowl/commands/owls_command.py` (share the SELECT); Test `tests/owls/test_dna_hydrator.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_dna_hydrator.py
import pytest

from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_hydrator import apply_dna_overlay, hydrate_dna
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry


def _reg():
    r = OwlRegistry.with_default_secretary()
    r.register(OwlAgentManifest(name="scout", role="research", system_prompt="P", model_tier="fast"))
    return r


def test_apply_dna_overlay_is_dna_only():
    r = _reg()
    ok = apply_dna_overlay(r, "scout", OwlDNA(curiosity=0.7))
    assert ok
    m = r.get("scout")
    assert m.curiosity_is := m.dna.curiosity == 0.7
    assert m.role == "research" and m.system_prompt == "P"   # identity untouched


@pytest.mark.asyncio
async def test_hydrate_dna_overlays_persisted_rows(tmp_db):
    r = _reg()
    await tmp_db.execute(
        "INSERT INTO owl_dna (owl_name, challenge_level, verbosity, curiosity, formality, "
        "creativity, precision, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("scout", 0.6, 0.5, 0.65, 0.5, 0.5, 0.5, "2026-06-06T00:00:00"),
    )
    n = await hydrate_dna(r, tmp_db)
    assert n == 1
    assert r.get("scout").dna.curiosity == 0.65


@pytest.mark.asyncio
async def test_hydrate_dna_failsafe_on_bad_row(tmp_db):
    r = _reg()
    await tmp_db.execute(
        "INSERT INTO owl_dna (owl_name, challenge_level, verbosity, curiosity, formality, "
        "creativity, precision, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("scout", 9.9, 0.5, 0.5, 0.5, 0.5, 0.5, "t"),   # out-of-range
    )
    await hydrate_dna(r, tmp_db)                          # must NOT crash
    assert 0.0 <= r.get("scout").dna.challenge_level <= 1.0   # clamped


@pytest.mark.asyncio
async def test_hydrate_skips_orphan_owl(tmp_db):
    r = _reg()
    await tmp_db.execute(
        "INSERT INTO owl_dna (owl_name, challenge_level, verbosity, curiosity, formality, "
        "creativity, precision, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("ghost", 0.6, 0.5, 0.5, 0.5, 0.5, 0.5, "t"),
    )
    n = await hydrate_dna(r, tmp_db)
    assert n == 0   # ghost not in registry → skipped, no crash
```
(`tmp_db` is the project conftest migrated-DB fixture. Drop the walrus line if it doesn't read cleanly — assert `m.dna.curiosity == 0.7`.)

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: Implement** `owls/dna_hydrator.py`:

```python
"""DNA hydration: overlay persisted owl_dna onto the live registry at boot.

apply_dna_overlay is the SINGLE DNA-only overlay primitive — reused by this
hydrator and by EvolutionCoordinator's live-refresh."""
from __future__ import annotations

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.owls.dna import _MUTABLE_TRAITS, OwlDNA
from stackowl.owls.registry import OwlRegistry

# Shared canonical read — owls_command lifts its SELECT to use this (DRY).
_SELECT_ALL_DNA = (
    "SELECT owl_name, challenge_level, verbosity, curiosity, formality, "
    "creativity, precision FROM owl_dna"
)


async def read_all_owl_dna(db: DbPool) -> dict[str, dict[str, float]]:
    rows = await db.fetch_all(_SELECT_ALL_DNA, ())
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out[str(r["owl_name"])] = {t: r[t] for t in _MUTABLE_TRAITS}
    return out


def apply_dna_overlay(registry: OwlRegistry, owl_name: str, dna: OwlDNA) -> bool:
    """DNA-only overlay: get current manifest → model_copy(dna) → replace. Returns
    False if the owl isn't registered (orphan)."""
    try:
        current = registry.get(owl_name)
    except Exception:
        return False
    registry.replace(current.model_copy(update={"dna": dna}))
    return True


def _coerce_dna(base: OwlDNA, row: dict[str, float]) -> OwlDNA:
    """Build a DNA from a persisted row: clamp each trait to [0,1]; NaN/inf/missing/
    non-numeric → keep base. (model_copy won't re-validate, so clamp explicitly.)"""
    updates: dict[str, float] = {}
    for trait in _MUTABLE_TRAITS:
        v = row.get(trait)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v or v in (float("inf"), float("-inf")):
            continue  # missing/NaN/inf/non-numeric → keep base default
        updates[trait] = max(0.0, min(1.0, float(v)))
    return base.model_copy(update=updates)


async def hydrate_dna(registry: OwlRegistry, db: DbPool) -> int:
    """Overlay persisted owl_dna onto registry manifests at boot. Fail-safe per row;
    one bad/orphan row never aborts the rest or crashes boot. Returns count hydrated."""
    log.startup.debug("[owls] hydrate_dna: entry")
    hydrated = 0
    try:
        all_dna = await read_all_owl_dna(db)
    except Exception as exc:  # whole-read failure → boot with authored DNA
        log.startup.warning("[owls] hydrate_dna: read failed — authored DNA", exc_info=exc)
        return 0
    for name, traits in all_dna.items():
        try:
            current = registry.get(name)
        except Exception:
            log.startup.warning("[owls] hydrate_dna: orphan owl_dna row skipped", extra={"_fields": {"owl": name}})
            continue
        try:
            registry.replace(current.model_copy(update={"dna": _coerce_dna(current.dna, traits)}))
            hydrated += 1
        except Exception as exc:  # corrupt row → keep authored DNA, loud
            log.startup.warning("[owls] hydrate_dna: row failed — authored DNA kept", exc_info=exc, extra={"_fields": {"owl": name}})
    log.startup.info("[owls] hydrate_dna: exit", extra={"_fields": {"hydrated": hydrated}})
    return hydrated
```

`owls_command.py` — replace its inline `_SELECT_DNA_SQL` usage's column knowledge by reusing the shared module (minimal: import `_MUTABLE_TRAITS`/the helper; keep its single-owl SELECT but ensure the column list isn't a third copy — at minimum add a comment pointing to `dna_hydrator._SELECT_ALL_DNA` as canonical, or refactor `_dna` to filter `read_all_owl_dna`). Keep changes minimal; do NOT break the display command.

- [ ] **Step 4: Run — verify PASS** (4) + regression `cd v2 && uv run pytest tests/owls/ -q`.

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/owls/dna_hydrator.py v2/src/stackowl/commands/owls_command.py v2/tests/owls/test_dna_hydrator.py
git commit -m "feat(v2): apply_dna_overlay + fail-safe DnaHydrator (persona-evo T2)"
```

---

### Task 3: wire hydration into the orchestrator

**Files:** Modify `src/stackowl/startup/orchestrator.py` (after `register_builtin_personas` at ~line 176 AND after `db_pool.open()` at ~line 179); Test `tests/startup/test_dna_hydration_wiring.py` (Create).

- [ ] **Step 1: Write the failing test**

```python
# tests/startup/test_dna_hydration_wiring.py
import inspect

from stackowl.startup import orchestrator


def test_orchestrator_calls_hydrate_dna():
    src = inspect.getsource(orchestrator)
    assert "hydrate_dna" in src   # the boot path invokes DNA hydration
```
(A light wiring guard — the real end-to-end proof is the T6 DNA-loop journey. Mirror any existing lightweight orchestrator-wiring test if one exists; otherwise this source-presence assertion is the minimal guard.)

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: Implement** — in `orchestrator.py`, after `db_pool.open()` (the registry + personas exist by line 176; db opens ~179), add:
```python
        from stackowl.owls.dna_hydrator import hydrate_dna

        await hydrate_dna(owl_registry, db_pool)
```
Place it after `await db_pool.open()` and before the scheduler/gateway phases (so the registry carries evolved DNA before any turn). Confirm `owl_registry` + `db_pool` are in scope there (recon: yes).

- [ ] **Step 4: Run — verify PASS** + `cd v2 && uv run python -c "import stackowl.startup.orchestrator"`.

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/startup/orchestrator.py v2/tests/startup/test_dna_hydration_wiring.py
git commit -m "feat(v2): wire DnaHydrator into orchestrator boot (persona-evo T3)"
```

---

### Task 4: EvolutionCoordinator — clamp → persist → refresh → audit

**Files:** Modify `src/stackowl/owls/evolution.py` (`_evolve_one` ~268-298, the persist site ~287); Test `tests/owls/test_evolution_feedback.py` (Create) + extend `tests/test_story_4_3.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_evolution_feedback.py
import pytest

from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry


@pytest.mark.asyncio
async def test_persist_then_refresh_updates_live_registry(tmp_db, monkeypatch):
    # build a coordinator with a registry; drive _evolve_one with known deltas;
    # assert (a) owl_dna persisted AND (b) registry.get(owl).dna reflects the bounded change.
    reg = OwlRegistry()
    reg.register(OwlAgentManifest(name="nora", role="r", system_prompt="p", model_tier="fast",
                                  dna=OwlDNA(curiosity=0.50)))
    # ... construct EvolutionCoordinator(tmp_db, provider_registry, reg, evolution_batch_size=1)
    # ... monkeypatch the delta computation to return {"curiosity": +0.5} (or use a MockProvider like test_story_4_3)
    # await coordinator._evolve_one(reg.get("nora"))
    live = reg.get("nora").dna.curiosity
    assert abs(live - 0.55) < 1e-9                 # bounded to +MAX_DELTA, AND live in registry
    rows = await tmp_db.fetch_all("SELECT curiosity FROM owl_dna WHERE owl_name='nora'", ())
    assert abs(rows[0]["curiosity"] - 0.55) < 1e-9  # persisted == live (clamp once, both)
```
**Implementer:** mirror `tests/test_story_4_3.py::test_execute_with_mock_llm_applies_mutations` for the coordinator construction + MockProvider delta injection + `tmp_db` + `_seed_messages`. The key new assertions over story_4_3: (1) `reg.get(owl).dna` changed LIVE (story_4_3 only checked SQLite); (2) the change is bounded by `bound_dna` (a big proposed delta is capped to MAX_DELTA).

- [ ] **Step 2: Run — verify FAIL** (registry.get(owl).dna unchanged today).

- [ ] **Step 3: Implement** — in `_evolve_one`, at the persist site (~287), wrap with the governor + refresh + audit. Current:
```python
        new_dna = manifest.dna
        for trait, delta in deltas.items():
            ...
            new_dna = new_dna.mutate(trait, delta)
        await self._persist_dna(manifest.name, new_dna)
```
Becomes:
```python
        new_dna = manifest.dna
        for trait, delta in deltas.items():
            ...
            new_dna = new_dna.mutate(trait, delta)
        safe_dna = bound_dna(manifest.dna, new_dna)            # governor (clamp once)
        await self._persist_dna(manifest.name, safe_dna)       # DB = source of truth (persist first)
        apply_dna_overlay(self._owl_registry, manifest.name, safe_dna)  # live refresh (next turn sees it)
        for trait in _MUTABLE_TRAITS:                          # audit (drift detectable + reversible)
            old, new = float(getattr(manifest.dna, trait)), float(getattr(safe_dna, trait))
            if old != new:
                log.owls.info("[owls] evolution.delta", extra={"_fields": {
                    "owl": manifest.name, "trait": trait, "old": old, "new": new,
                    "delta": round(new - old, 4), "source": source, "batch_id": job.id}})
```
Imports: `from stackowl.owls.dna_governor import bound_dna`, `from stackowl.owls.dna_hydrator import apply_dna_overlay`, `from stackowl.owls.dna import _MUTABLE_TRAITS`. `source` = "attribution"/"llm_fallback" (already known in `_evolve_one` — use the existing variable; confirm its name). `job.id` is the batch id (confirm the Job field). Persist BEFORE refresh (crash-safe). Confirm `log.owls` exists (else use the namespace `_evolve_one` already logs with).

NOTE: `apply_dna_overlay` re-fetches the current manifest inside itself (get→model_copy→replace), so a concurrent owl-builder identity edit isn't clobbered. Persist uses `safe_dna` (not raw `new_dna`) so persisted == live.

- [ ] **Step 4: Run — verify PASS** + regression `cd v2 && uv run pytest tests/test_story_4_3.py tests/owls/ -q` (story_4_3's SQLite assertions still hold — values now bounded; if story_4_3 asserts an exact pre-governor value that the governor would change, update it to the bounded value and note it — do NOT weaken).

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/owls/evolution.py v2/tests/owls/test_evolution_feedback.py v2/tests/test_story_4_3.py
git commit -m "feat(v2): EvolutionCoordinator clamp->persist->refresh->audit (persona-evo T4)"
```

---

## STORY 4b — Memory gaps

### Task 5: FactPromoter embedding completeness

**Files:** Modify `src/stackowl/memory/fact_promoter.py` (`__init__` 65-98; `_promote_one` ~205-262, before line 208); `src/stackowl/memory/assembly.py` (~155) + `src/stackowl/tools/knowledge/memory.py` (~189) (pass `embedding_registry`); Test `tests/memory/test_promoter_embed.py` (Create).

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_promoter_embed.py
import pytest

from stackowl.memory.fact_promoter import FactPromoter
# import the staged-fact insert helper / StagedFact as test_story_6_3 does


class _StubProvider:
    model_name = "stub"
    async def embed(self, texts): return [[0.1, 0.2, 0.3] for _ in texts]


class _StubEmbReg:
    def get(self): return _StubProvider()


@pytest.mark.asyncio
async def test_force_promote_embeds_when_staged_fact_has_no_vector(tmp_db):
    # seed a staged fact WITHOUT an embedding (mirror test_story_6_3._insert_staged with embedding=None)
    ...  # insert staged fact id=1, content="x", embedding=None
    captured = {}
    class _Lance:
        async def upsert(self, fid, vec, meta): captured["vec"] = vec
    promoter = FactPromoter(tmp_db, lancedb=_Lance(), embedding_registry=_StubEmbReg())
    ok = await promoter.force_promote(1)
    assert ok
    assert captured.get("vec") == [0.1, 0.2, 0.3]   # embedding computed + upserted to LanceDB


@pytest.mark.asyncio
async def test_promote_failopen_when_no_embedding_registry(tmp_db):
    # no embedding_registry → FTS-only, no crash (today's behavior preserved)
    ...  # insert staged fact id=2, embedding=None
    promoter = FactPromoter(tmp_db)                  # no embedding_registry
    assert await promoter.force_promote(2) is True   # promotes, no crash
```
**Implementer:** mirror `tests/test_story_6_3.py` for the `tmp_db`/`_insert_staged`/StagedFact shapes (`StagedFact.content`, `.embedding`, frozen). Confirm `force_promote(id)` signature.

- [ ] **Step 2: Run — verify FAIL** (no embedding computed; `embedding_registry` param missing).

- [ ] **Step 3: Implement**

`FactPromoter.__init__` — add `embedding_registry: EmbeddingRegistry | None = None` (store `self._embedding_registry`). Import under TYPE_CHECKING.

`_promote_one` — before line 208 (`embedding_blob = pack_embedding(...)`), compute a missing embedding (reuse `_best_effort_embed`, fail-open, rebuild the frozen fact):
```python
        if fact.embedding is None and self._embedding_registry is not None:
            from stackowl.commands.memory_helpers import _best_effort_embed
            vec, model = await _best_effort_embed(fact.content, self._embedding_registry)
            if vec is not None:
                fact = fact.model_copy(update={"embedding": vec, "embedding_model": model})
```
(Guard `is None` → no double-embed. `_best_effort_embed` never raises → fail-open: vec None → stays FTS-only. `fact.content` is the text field. The existing `embedding_blob`/LanceDB branches then naturally pick up the new `fact.embedding`.)

`memory/assembly.py:155` + `tools/knowledge/memory.py:189` — pass `embedding_registry=` (and `lancedb=` at the assembly site if not already) to the `FactPromoter(...)` constructor (the registry is in scope at both — `MemoryAssembly` has `embedding_registry`; the tool has `get_services().embedding_registry`).

- [ ] **Step 4: Run — verify PASS** (2) + regression `cd v2 && uv run pytest tests/memory/ tests/test_story_6_3.py -q`.

- [ ] **Step 5: Commit**

```bash
git add v2/src/stackowl/memory/fact_promoter.py v2/src/stackowl/memory/assembly.py v2/src/stackowl/tools/knowledge/memory.py v2/tests/memory/test_promoter_embed.py
git commit -m "feat(v2): FactPromoter embeds missing vectors on promote (persona-evo T5)"
```

---

### Task 6: Journeys — cross-session recall + DNA loop + slow-poison

**Files:** Create `tests/journeys/test_persona_evolution_journey.py` (or `tests/smoke/`). Mirror `tests/smoke/test_e4_s1_memory_telegram_smoke.py` (memory round-trip) + `tests/test_story_4_3.py` (evolution).

- [ ] **Step 1: Write the journeys**

```python
# tests/journeys/test_persona_evolution_journey.py
import pytest


@pytest.mark.asyncio
async def test_cross_session_recall(...):
    """Session A remembers a fact (staged, no vector) → a DETERMINISTIC DreamWorker
    promote pass (call promoter.promote_eligible directly; Clock past settle window;
    a fake embedder for exact cosine) → session B recalls it semantically."""
    ...


@pytest.mark.asyncio
async def test_dna_evolution_reaches_persona_live_and_after_restart(...):
    """Run an evolution batch (MockProvider deltas) → assert registry.get(owl).dna
    changed LIVE → build a FRESH registry + hydrate_dna(fresh, same_db) (simulated
    restart) → assert the evolved DNA reproduced AND DNAPromptInjector emits it."""
    ...


@pytest.mark.asyncio
async def test_slow_poison_floor_holds(...):
    """Drive several evolution batches all pushing challenge_level -> 0 → bound_dna's
    floor holds (challenge_level never drops below TRAIT_FLOOR) → the persona never
    crosses into the <0.3 'no pushback' directive zone. Proves the governor e2e."""
    ...
```
**Implementer:** build on the e4_s1 memory smoke + story_4_3 evolution harnesses; mock ONLY the AI provider. The cross-session journey must use the REAL FactPromoter (proving T5) + a deterministic embedder + a direct `promote_eligible()` call (NOT the scheduler — never poll). The DNA journey proves persist→live + persist→hydrate (simulated restart). The slow-poison journey proves the floor governor end-to-end. If a journey can't pass without new production code, STOP and report (don't silently patch/weaken).

- [ ] **Step 2–4:** run → FAIL (right reason) → wire minimal harness (existing components) → PASS.

- [ ] **Step 5: Commit**

```bash
git add v2/tests/journeys/test_persona_evolution_journey.py
git commit -m "test(v2): persona-evolution journeys — cross-session recall + DNA loop + slow-poison floor (persona-evo T6)"
```

---

## Final verification

- [ ] `cd v2 && uv run pytest tests/owls/test_dna_governor.py tests/owls/test_dna_hydrator.py tests/owls/test_evolution_feedback.py tests/startup/test_dna_hydration_wiring.py tests/memory/test_promoter_embed.py tests/journeys/test_persona_evolution_journey.py tests/test_story_4_3.py tests/test_story_6_3.py -v`
- [ ] `cd v2 && uv run ruff check src/ && uv run mypy src/stackowl/owls/ src/stackowl/memory/fact_promoter.py`
- [ ] Regression: `cd v2 && uv run pytest tests/owls tests/memory tests/pipeline/test_plan_a_assemble.py -q`
- [ ] Final reviewer → merge to main + push (standing prefs).

---

## Spec coverage self-check

| Spec element | Task |
|---|---|
| `_bound_dna` governor (clamp/max-delta/envelope/floors) | T1 |
| `apply_dna_overlay` shared primitive | T2 |
| fail-safe `DnaHydrator` (boot, corrupt/orphan-safe) | T2 |
| hydration wired at boot | T3 |
| EvolutionCoordinator clamp→persist→refresh→audit | T4 |
| live = next-turn (persist-then-refresh; per-turn snapshot already holds — recon §6) | T4 (+ snapshot is pre-existing/verified) |
| DNA ≠ authz (personality only) | inherent (no bounds touched) |
| force_promote/_promote_one embedding completeness | T5 |
| cross-session recall + DNA loop + slow-poison journeys | T6 |
| DEFERRED: true Schmitt, reset cmd, memory-promotion injection governance, writer-writer lock, authored-non-neutral envelope anchor | not in plan ✓ |
