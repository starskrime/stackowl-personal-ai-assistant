# DNA-Evolution Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the owl persona-evolution loop — authored-anchored evolution envelope, a DnaDefaults DRY holder, Schmitt-trigger hysteresis on the directive injector, and `/owls reset-dna` — so a personality grows but stays itself and is recoverable.

**Architecture:** A durable `owl_dna_authored` table (migration 0051) captures each owl's YAML-authored DNA at boot (before `hydrate_dna` overwrites the live manifest) and at creation. `bound_dna` re-centers the evolution envelope on that authored anchor (`anchor ± ENVELOPE`) with an author-deferring floor (`min(TRAIT_FLOOR, anchor)`). A `DnaDefaults` module kills the scattered neutral/trait-list duplication behind a no-op-proof test. An in-memory module-singleton `DIRECTIVE_LATCH` adds Schmitt hysteresis (enter 0.70 / exit 0.60) to the directive injector. `/owls reset-dna` reverts evolved→authored, live-refreshes the registry, and clears the latch.

**Tech Stack:** Python 3.11+, Pydantic v2 (frozen), asyncio, SQLite (raw-SQL numbered migrations under `src/stackowl/db/migrations/`), pytest, ruff, mypy --strict. Run from `v2/`: `uv run pytest <path> -v` (NO `--timeout`; targeted paths only — full suite hangs).

**Spec:** `docs/superpowers/specs/2026-06-06-dna-evolution-completion-design.md` (read first).

**Standing rules (memory — non-negotiable):** check existing before writing new (reuse `_coerce_dna`, `apply_dna_overlay`, the `_UPSERT_DNA_SQL` shape, the `FOCUS_TRACKER` pattern, the `_remove` confirm pattern — do NOT recreate); no silent errors (every `except` logs via `log.engine`); no hardcoded English keywords; minimal changes; migration-runner gotchas (no `;` in SQL comments; no non-constant DEFAULT; literal `'principal-default'` must equal `DEFAULT_PRINCIPAL_ID`); commit per sub-task; stage `v2/` only; never pipe pytest to `tail` in a `&&` chain.

---

## Reuse Ledger

| Need | Existing thing | Location |
|---|---|---|
| Canonical trait order | `_MUTABLE_TRAITS` = (challenge_level, verbosity, curiosity, formality, creativity, precision) | `owls/dna.py:10` |
| DNA coercion (NaN/inf/clamp) | `_coerce_dna(base, row)` | `owls/dna_hydrator.py:53` |
| Live registry refresh | `apply_dna_overlay(registry, name, dna)` | `owls/dna_hydrator.py:25` |
| owl_dna upsert SQL (to extract) | `_UPSERT_DNA_SQL` + `_persist_dna` | `owls/evolution.py:49,414` |
| In-memory singleton pattern | `SkillFocusTracker`/`FOCUS_TRACKER` (Lock, _MAX_KEYS, fail-safe) | `skills/skill_focus.py` |
| YES-confirm command pattern | `_remove` | `commands/owls_command.py:206` |
| dna readout | `_dna` + `_SELECT_DNA_SQL` + `format_dna_display` | `commands/owls_command.py:253,53` |
| Governor | `bound_dna(current, proposed)` (1 prod call @ evolution.py:289) | `owls/dna_governor.py:19` |
| Limits | `DNA_NEUTRAL/MAX_DELTA/ENVELOPE/TRAIT_FLOOR/FLOOR_TRAITS` | `owls/evolution_limits.py` |
| DB API | `db.execute(sql, params)` / `db.fetch_all(sql, params)->list[dict]` (NO fetch_one) | `db/pool.py` |
| Principal literal | `DEFAULT_PRINCIPAL_ID = "principal-default"` | `tenancy/principal.py:25` |
| Migration template | `0011_owl_dna.sql`; owner_id pattern in `0043` | `db/migrations/` |

---

### Task 1: `DnaDefaults` holder + no-op-proof test (lands BEFORE any repoint)

**Files:**
- Create: `src/stackowl/owls/dna_defaults.py`
- Test: `tests/owls/test_dna_defaults.py` (create)

- [ ] **Step 1: Write the proof test** (asserts the NEW constant equals every CURRENT live list, in order, against a frozen literal)

```python
# tests/owls/test_dna_defaults.py
import re
from stackowl.owls.dna_defaults import NEUTRAL, TRAIT_NAMES
from stackowl.owls.dna import _MUTABLE_TRAITS
from stackowl.owls.evolution import DeltaValidator
from stackowl.owls.dna_storage import _DNA_FIELDS
from stackowl.owls.evolution_limits import DNA_NEUTRAL
from stackowl.owls.dna_hydrator import _SELECT_ALL_DNA

# The canonical truth, frozen here so a future "improvement" to the list fails loudly.
_EXPECTED = ("challenge_level", "verbosity", "curiosity", "formality", "creativity", "precision")


def test_canonical_traits_and_neutral():
    assert TRAIT_NAMES == _EXPECTED
    assert len(TRAIT_NAMES) == 6
    assert NEUTRAL == 0.5


def test_all_live_sites_equal_canonical_in_order():
    # ordered tuple sites
    assert tuple(_MUTABLE_TRAITS) == _EXPECTED
    assert tuple(_DNA_FIELDS) == _EXPECTED
    # set site (order-irrelevant, but membership must match)
    assert frozenset(DeltaValidator._TRAITS) == frozenset(_EXPECTED)
    # neutral
    assert DNA_NEUTRAL == NEUTRAL == 0.5


def test_sql_column_order_matches_canonical():
    # parse the trait columns out of `SELECT owl_name, <traits...> FROM owl_dna`
    cols = re.search(r"SELECT\s+(.*?)\s+FROM", _SELECT_ALL_DNA, re.S).group(1)
    names = [c.strip() for c in cols.replace("\n", " ").split(",")]
    traits = tuple(n for n in names if n != "owl_name")
    assert traits == _EXPECTED  # positional-unpacking transposition guard
```

- [ ] **Step 2: Run, verify it FAILS** — `uv run pytest tests/owls/test_dna_defaults.py -v` → FAIL (`dna_defaults` missing).

- [ ] **Step 3: Create `src/stackowl/owls/dna_defaults.py`**

```python
"""Single source of truth for the DNA neutral value and the canonical trait order.
Kills the scattered 0.5 (8+ sites) and trait-list (6+ sites) duplication. Story C."""
from __future__ import annotations

NEUTRAL: float = 0.5
TRAIT_NAMES: tuple[str, ...] = (
    "challenge_level", "verbosity", "curiosity", "formality", "creativity", "precision",
)
```

- [ ] **Step 4: Run, verify PASS** (3 tests). This proves the new constant matches every existing site BEFORE any repoint.

- [ ] **Step 5: Commit**

```bash
cd v2 && uv run mypy src/stackowl/owls/dna_defaults.py && uv run ruff check src/stackowl/owls/dna_defaults.py
cd /ssd/projects/stackowl-personal-ai-assistant
git add v2/src/stackowl/owls/dna_defaults.py v2/tests/owls/test_dna_defaults.py
git commit -m "feat(v2): DnaDefaults holder (NEUTRAL + canonical trait list) + no-op proof — dna-completion C"
```

---

### Task 2: Repoint the Python dup sites to `DnaDefaults` (pure no-op)

**Files:**
- Modify: `src/stackowl/owls/dna.py:10` (`_MUTABLE_TRAITS`), `:30-36` (Field defaults → `NEUTRAL`), `:86` (`- 0.5`)
- Modify: `src/stackowl/owls/evolution_limits.py:6` (`DNA_NEUTRAL`)
- Modify: `src/stackowl/owls/evolution.py:77` (`DeltaValidator._TRAITS`)
- Modify: `src/stackowl/owls/dna_storage.py:37` (`_DNA_FIELDS`)

- [ ] **Step 1: Repoint, one site at a time, re-running the Task-1 test after each.** The Task-1 test is the regression guard (it must stay green throughout).

`dna.py` — add `from stackowl.owls.dna_defaults import NEUTRAL, TRAIT_NAMES`, then:
```python
_MUTABLE_TRAITS: tuple[str, ...] = TRAIT_NAMES
```
and the 6 Field defaults:
```python
challenge_level: float = Field(default=NEUTRAL, ge=0.0, le=1.0)
verbosity: float = Field(default=NEUTRAL, ge=0.0, le=1.0)
curiosity: float = Field(default=NEUTRAL, ge=0.0, le=1.0)
formality: float = Field(default=NEUTRAL, ge=0.0, le=1.0)
creativity: float = Field(default=NEUTRAL, ge=0.0, le=1.0)
precision: float = Field(default=NEUTRAL, ge=0.0, le=1.0)
```
and `dominant_traits` line 86 `abs(... - 0.5)` → `abs(... - NEUTRAL)`. (Leave `decay_rate_per_week` default 0.05 unchanged.)

`evolution_limits.py:6` — add `from stackowl.owls.dna_defaults import NEUTRAL` and `DNA_NEUTRAL = NEUTRAL`.

`evolution.py` — `from stackowl.owls.dna_defaults import TRAIT_NAMES`; `DeltaValidator._TRAITS = frozenset(TRAIT_NAMES)`.

`dna_storage.py` — `from stackowl.owls.dna_defaults import TRAIT_NAMES`; `_DNA_FIELDS = TRAIT_NAMES` (confirm `_DNA_FIELDS` is used the same way — a tuple iteration; if it's typed/annotated differently, keep the type).

> Do NOT touch any SQL DDL or SQL column strings (they stay literal; the Task-1 test guards their order). Do NOT change `decay_rate_per_week`.

- [ ] **Step 2: Run the guard + the DNA neighborhood**

```
uv run pytest tests/owls/test_dna_defaults.py tests/owls/test_dna.py tests/owls/test_dna_governor.py tests/owls/test_dna_hydrator.py tests/owls/test_evolution_feedback.py -v
```
Expected: ALL still pass (pure no-op). If any value/behavior changed, you mis-repointed — revert that site.

- [ ] **Step 3: mypy + ruff on the 4 modified files; commit**

```bash
cd v2 && uv run mypy src/stackowl/owls/dna.py src/stackowl/owls/evolution_limits.py src/stackowl/owls/evolution.py src/stackowl/owls/dna_storage.py && uv run ruff check src/stackowl/owls/dna.py src/stackowl/owls/evolution_limits.py src/stackowl/owls/evolution.py src/stackowl/owls/dna_storage.py
cd /ssd/projects/stackowl-personal-ai-assistant
git add v2/src/stackowl/owls/dna.py v2/src/stackowl/owls/evolution_limits.py v2/src/stackowl/owls/evolution.py v2/src/stackowl/owls/dna_storage.py
git commit -m "refactor(v2): repoint DNA neutral + trait-list dup sites to DnaDefaults (no-op) — dna-completion C"
```

---

### Task 3: Migration `0051` (authored table) + extract shared `upsert_owl_dna`

**Files:**
- Create: `src/stackowl/db/migrations/0051_owl_dna_authored.sql`
- Modify: `src/stackowl/owls/dna_storage.py` (add `upsert_owl_dna`)
- Modify: `src/stackowl/owls/evolution.py` (repoint `_persist_dna` to the shared helper)
- Test: `tests/owls/test_dna_upsert.py` (create)

- [ ] **Step 1: Write the migration** (mirror `0011` + the `0043` owner_id convention; owl_dna is owner-blind on `owl_name`, so the authored table matches it exactly)

```sql
-- Migration 0051 owl_dna_authored
-- Durable per-owl AUTHORED (baseline) DNA, captured from the YAML/manifest at boot
-- before evolved DNA is hydrated. The envelope anchor and reset-dna target.
CREATE TABLE IF NOT EXISTS owl_dna_authored (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owl_name TEXT NOT NULL UNIQUE,
    challenge_level REAL NOT NULL DEFAULT 0.5,
    verbosity REAL NOT NULL DEFAULT 0.5,
    curiosity REAL NOT NULL DEFAULT 0.5,
    formality REAL NOT NULL DEFAULT 0.5,
    creativity REAL NOT NULL DEFAULT 0.5,
    precision REAL NOT NULL DEFAULT 0.5,
    owner_id TEXT NOT NULL DEFAULT 'principal-default',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_owl_dna_authored_owner ON owl_dna_authored(owner_id);
```

- [ ] **Step 2: Write the failing upsert round-trip test (distinct values per trait — never all-0.5)**

```python
# tests/owls/test_dna_upsert.py
import pytest
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_storage import upsert_owl_dna


@pytest.mark.asyncio
async def test_upsert_round_trips_distinct_values(tmp_db):
    dna = OwlDNA(challenge_level=0.11, verbosity=0.22, curiosity=0.33,
                 formality=0.44, creativity=0.55, precision=0.66)
    await upsert_owl_dna(tmp_db, "scout", dna, table="owl_dna")
    rows = await tmp_db.fetch_all("SELECT * FROM owl_dna WHERE owl_name = ?", ("scout",))
    r = rows[0]
    assert (r["challenge_level"], r["verbosity"], r["curiosity"], r["formality"],
            r["creativity"], r["precision"]) == (0.11, 0.22, 0.33, 0.44, 0.55, 0.66)


@pytest.mark.asyncio
async def test_upsert_into_authored_table(tmp_db):
    dna = OwlDNA(challenge_level=0.7)
    await upsert_owl_dna(tmp_db, "scout", dna, table="owl_dna_authored")
    rows = await tmp_db.fetch_all("SELECT challenge_level FROM owl_dna_authored WHERE owl_name = ?", ("scout",))
    assert rows[0]["challenge_level"] == 0.7


@pytest.mark.asyncio
async def test_upsert_is_idempotent(tmp_db):
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(verbosity=0.3), table="owl_dna")
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(verbosity=0.8), table="owl_dna")
    rows = await tmp_db.fetch_all("SELECT verbosity FROM owl_dna WHERE owl_name = ?", ("scout",))
    assert len(rows) == 1 and rows[0]["verbosity"] == 0.8
```

(`tmp_db` is the shared conftest fixture that runs all migrations — it will pick up 0051.)

- [ ] **Step 3: Run, verify FAIL** (`upsert_owl_dna` missing; authored table is created by the migration).

- [ ] **Step 4: Implement `upsert_owl_dna` in `dna_storage.py`** (parameterized by table; positional binds in canonical order)

```python
from datetime import UTC, datetime
from stackowl.owls.dna_defaults import TRAIT_NAMES

_ALLOWED_DNA_TABLES = frozenset({"owl_dna", "owl_dna_authored"})


async def upsert_owl_dna(db: "DbPool", owl_name: str, dna: "OwlDNA", *, table: str = "owl_dna") -> None:
    """Upsert the 6 trait columns + updated_at for an owl into `table` (owl_dna or owl_dna_authored).
    Single source for both the evolved store and the authored store (DRY)."""
    if table not in _ALLOWED_DNA_TABLES:
        raise ValueError(f"upsert_owl_dna: unknown table {table!r}")
    cols = ", ".join(TRAIT_NAMES)
    placeholders = ", ".join("?" for _ in TRAIT_NAMES)
    set_clause = ", ".join(f"{t} = excluded.{t}" for t in TRAIT_NAMES)
    sql = (
        f"INSERT INTO {table} (owl_name, {cols}, updated_at) "
        f"VALUES (?, {placeholders}, ?) "
        f"ON CONFLICT(owl_name) DO UPDATE SET {set_clause}, updated_at = excluded.updated_at"
    )
    values = (owl_name, *(float(getattr(dna, t)) for t in TRAIT_NAMES), datetime.now(UTC).isoformat())
    await db.execute(sql, values)
```

> `table` is from a fixed allowlist (not user input) — the f-string interpolation is safe. The trait columns/values use `TRAIT_NAMES` so column-order can never transpose (it's the single canonical order). Confirm the `DbPool`/`OwlDNA` imports/type-hints match the module (use TYPE_CHECKING if needed to avoid cycles).

- [ ] **Step 5: Repoint `evolution._persist_dna` to the helper** (kill the duplicate `_UPSERT_DNA_SQL`)

In `evolution.py`, replace the `_persist_dna` body with:
```python
    async def _persist_dna(self, owl_name: str, dna: OwlDNA) -> None:
        await upsert_owl_dna(self._db, owl_name, dna, table="owl_dna")
```
Add `from stackowl.owls.dna_storage import upsert_owl_dna`. You may delete `_UPSERT_DNA_SQL` (now unused) — confirm no other reference first (grep). Keep the call site at line 290 unchanged.

- [ ] **Step 6: Run tests** — `uv run pytest tests/owls/test_dna_upsert.py tests/owls/test_evolution_feedback.py -v` (the evolution tests prove the repointed persist still works).

- [ ] **Step 7: mypy + ruff; commit**

```bash
cd v2 && uv run mypy src/stackowl/owls/dna_storage.py src/stackowl/owls/evolution.py && uv run ruff check src/stackowl/owls/dna_storage.py src/stackowl/owls/evolution.py
cd /ssd/projects/stackowl-personal-ai-assistant
git add v2/src/stackowl/db/migrations/0051_owl_dna_authored.sql v2/src/stackowl/owls/dna_storage.py v2/src/stackowl/owls/evolution.py v2/tests/owls/test_dna_upsert.py
git commit -m "feat(v2): migration 0051 authored-DNA table + shared upsert_owl_dna (DRY) — dna-completion C"
```

---

### Task 4: Authored-DNA store (capture + read)

**Files:**
- Create: `src/stackowl/owls/dna_authored.py`
- Test: `tests/owls/test_dna_authored.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_dna_authored.py
import pytest
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_authored import capture_one_authored, capture_authored_dna, read_authored_dna
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.manifest import OwlAgentManifest


def _reg_with(name, dna):
    reg = OwlRegistry()
    reg.register(OwlAgentManifest(name=name, role=name, system_prompt="p", model_tier="fast", dna=dna), source_name="t")
    return reg


@pytest.mark.asyncio
async def test_capture_then_read_round_trip(tmp_db):
    dna = OwlDNA(challenge_level=0.75, precision=0.66)
    await capture_one_authored(tmp_db, "scout", dna)
    got = await read_authored_dna(tmp_db, "scout")
    assert got is not None and got.challenge_level == 0.75 and got.precision == 0.66


@pytest.mark.asyncio
async def test_read_missing_returns_none(tmp_db):
    assert await read_authored_dna(tmp_db, "ghost") is None


@pytest.mark.asyncio
async def test_capture_authored_dna_boot_pass_covers_all_owls(tmp_db):
    reg = _reg_with("scout", OwlDNA(curiosity=0.8))
    await capture_authored_dna(reg, tmp_db)
    got = await read_authored_dna(tmp_db, "scout")
    assert got is not None and got.curiosity == 0.8


@pytest.mark.asyncio
async def test_recreate_same_name_overwrites_anchor(tmp_db):
    await capture_one_authored(tmp_db, "scout", OwlDNA(verbosity=0.2))
    await capture_one_authored(tmp_db, "scout", OwlDNA(verbosity=0.9))  # re-authored
    got = await read_authored_dna(tmp_db, "scout")
    assert got.verbosity == 0.9


@pytest.mark.asyncio
async def test_read_coerces_corrupt_row(tmp_db):
    # raw-insert a NaN, confirm read coerces to a valid OwlDNA (clamped/neutral), never crashes
    await tmp_db.execute(
        "INSERT INTO owl_dna_authored (owl_name, challenge_level, verbosity, curiosity, formality, creativity, precision, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("bad", float("nan"), 0.5, 0.5, 0.5, 0.5, 0.5, "t"))
    got = await read_authored_dna(tmp_db, "bad")
    assert got is not None and 0.0 <= got.challenge_level <= 1.0  # coerced
```

- [ ] **Step 2: Run, verify FAIL** (module missing).

- [ ] **Step 3: Implement `src/stackowl/owls/dna_authored.py`**

```python
"""Durable AUTHORED (baseline) DNA store: the envelope anchor + reset-dna target.
Captured from the YAML/manifest at boot (before hydrate overwrites the live DNA) and at
owl creation. Reuses _coerce_dna + upsert_owl_dna (DRY). Fail-safe per owl."""
from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_defaults import TRAIT_NAMES
from stackowl.owls.dna_hydrator import _coerce_dna
from stackowl.owls.dna_storage import upsert_owl_dna

if TYPE_CHECKING:
    from stackowl.db.pool import DbPool
    from stackowl.owls.registry import OwlRegistry

_SELECT_AUTHORED = (
    "SELECT challenge_level, verbosity, curiosity, formality, creativity, precision "
    "FROM owl_dna_authored WHERE owl_name = ?"
)


async def capture_one_authored(db: "DbPool", owl_name: str, dna: OwlDNA) -> None:
    """Idempotent upsert of one owl's authored DNA. manifest.dna is already a validated
    OwlDNA, so this can't write garbage; a broken YAML owl simply never reaches here
    (it wouldn't be in the registry). Fail-safe — logs and continues."""
    try:
        await upsert_owl_dna(db, owl_name, dna, table="owl_dna_authored")
    except Exception as exc:
        log.engine.error("[owls] capture_one_authored failed", exc_info=exc, extra={"_fields": {"owl": owl_name}})


async def capture_authored_dna(registry: "OwlRegistry", db: "DbPool") -> int:
    """Boot pass: capture each registered owl's authored DNA BEFORE hydrate overwrites it.
    Idempotent (re-running yields identical rows). Returns count captured."""
    captured = 0
    for manifest in list(registry.all()):
        try:
            await capture_one_authored(db, manifest.name, manifest.dna)
            captured += 1
        except Exception as exc:  # one bad owl never aborts boot
            log.engine.error("[owls] capture_authored_dna: owl failed", exc_info=exc, extra={"_fields": {"owl": manifest.name}})
    return captured


async def read_authored_dna(db: "DbPool", owl_name: str) -> OwlDNA | None:
    """Read an owl's authored DNA, coerced (NaN/inf/out-of-range guarded). None if no row."""
    try:
        rows = await db.fetch_all(_SELECT_AUTHORED, (owl_name,))
    except Exception as exc:
        log.engine.error("[owls] read_authored_dna failed", exc_info=exc, extra={"_fields": {"owl": owl_name}})
        return None
    if not rows:
        return None
    row = {t: rows[0][t] for t in TRAIT_NAMES}
    return _coerce_dna(OwlDNA(), row)
```

- [ ] **Step 4: Run, verify PASS** (5 tests).

- [ ] **Step 5: mypy + ruff; commit**

```bash
cd v2 && uv run mypy src/stackowl/owls/dna_authored.py && uv run ruff check src/stackowl/owls/dna_authored.py
cd /ssd/projects/stackowl-personal-ai-assistant
git add v2/src/stackowl/owls/dna_authored.py v2/tests/owls/test_dna_authored.py
git commit -m "feat(v2): authored-DNA store (capture/read, coerced, fail-safe) — dna-completion C"
```

---

### Task 5: Wire authored capture into boot + creation seams

**Files:**
- Modify: `src/stackowl/startup/orchestrator.py:180-182` (capture before hydrate)
- Modify: `src/stackowl/commands/owls_command.py:157` (`_add` captures at creation)
- Modify: `src/stackowl/tools/meta/owl_build.py:~337` (`_create` captures at creation)
- Test: `tests/startup/test_authored_capture_wiring.py` (create)

- [ ] **Step 1: Write the failing test** (source-inspection wiring, mirroring `test_orchestrator_owl_revalidate`)

```python
# tests/startup/test_authored_capture_wiring.py
import inspect
from stackowl.startup import orchestrator


def test_capture_authored_runs_before_hydrate():
    src = inspect.getsource(orchestrator)
    assert "capture_authored_dna" in src
    assert src.index("capture_authored_dna") < src.index("hydrate_dna("), "authored capture must precede hydrate"
```

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3a: Boot wiring** — in `orchestrator.py`, immediately BEFORE `await hydrate_dna(owl_registry, db_pool)` (line 182):

```python
        from stackowl.owls.dna_authored import capture_authored_dna

        await capture_authored_dna(owl_registry, db_pool)
```

- [ ] **Step 3b: `_add` creation seam** — in `owls_command.py` after `self._registry.register(manifest)` (line 157), with `self._db` available:

```python
        if self._db is not None:
            from stackowl.owls.dna_authored import capture_one_authored

            await capture_one_authored(self._db, manifest.name, manifest.dna)
```

- [ ] **Step 3c: `owl_build._create` seam** — after the successful `registry.register(manifest, source_name=_SOURCE_NAME)` (line ~337), with `svc = get_services()` already in scope (its `db_pool`):

```python
            if svc.db_pool is not None:
                from stackowl.owls.dna_authored import capture_one_authored

                await capture_one_authored(svc.db_pool, manifest.name, manifest.dna)
```

> Confirm `svc.db_pool` is the real attribute (recon: `StepServices.db_pool`). Place inside the existing success path / rollback-safe region (a capture failure is fail-safe inside `capture_one_authored`, so it won't break creation). Match the surrounding async/await + indentation.

- [ ] **Step 4: Run tests**

```
uv run pytest tests/startup/test_authored_capture_wiring.py tests/commands/test_owls_builder.py tests/tools/meta/test_owl_build_gateway.py -v
```
Expected: wiring test passes; existing add/owl_build tests stay green.

- [ ] **Step 5: mypy + ruff (note pre-existing errors); commit**

```bash
cd v2 && uv run mypy src/stackowl/startup/orchestrator.py src/stackowl/commands/owls_command.py src/stackowl/tools/meta/owl_build.py && uv run ruff check src/stackowl/startup/orchestrator.py src/stackowl/commands/owls_command.py src/stackowl/tools/meta/owl_build.py
cd /ssd/projects/stackowl-personal-ai-assistant
git add v2/src/stackowl/startup/orchestrator.py v2/src/stackowl/commands/owls_command.py v2/src/stackowl/tools/meta/owl_build.py v2/tests/startup/test_authored_capture_wiring.py
git commit -m "feat(v2): capture authored DNA at boot (pre-hydrate) + creation seams — dna-completion C"
```

---

### Task 6: Authored-anchor envelope in `bound_dna`

**Files:**
- Modify: `src/stackowl/owls/dna_governor.py` (`bound_dna` gains `anchor`)
- Modify: `src/stackowl/owls/evolution.py:289` (fetch anchor, pass it)
- Modify: `tests/owls/test_dna_governor.py` (update 7 calls to pass a neutral anchor)
- Test: add anchor-specific tests to `tests/owls/test_dna_governor.py`

- [ ] **Step 1: Write the new failing tests** (append to `tests/owls/test_dna_governor.py`)

```python
from stackowl.owls.dna_governor import bound_dna


def test_envelope_recenters_on_anchor():
    # authored high (0.75) → band [0.45, 1.0]; a proposed 0.95 rate-caps to 0.55 from current 0.5...
    # use current=0.74 so rate cap (0.05) lands 0.79, INSIDE the anchored band (would be clamped to 0.8 under old neutral envelope)
    anchor = OwlDNA(challenge_level=0.75)
    out = bound_dna(OwlDNA(challenge_level=0.74), OwlDNA(challenge_level=0.95), anchor)
    assert abs(out.challenge_level - 0.79) < 1e-9  # within [0.45,1.0], NOT capped at old 0.8


def test_anchor_floor_defers_to_author_below_floor():
    # authored precision 0.1 → effective floor min(0.3, 0.1) = 0.1; evolution can't go below 0.1 but may sit there
    anchor = OwlDNA(precision=0.1)
    out = bound_dna(OwlDNA(precision=0.12), OwlDNA(precision=0.0), anchor)
    assert abs(out.precision - 0.1) < 1e-9  # floored to authored 0.1, not 0.3


def test_anchor_floor_normal_when_author_above_floor():
    anchor = OwlDNA(precision=0.5)
    out = bound_dna(OwlDNA(precision=0.32), OwlDNA(precision=0.0), anchor)
    assert out.precision >= 0.3  # standard TRAIT_FLOOR applies


def test_neutral_anchor_preserves_legacy_envelope():
    # anchor=neutral 0.5 → band [0.2,0.8] (the old behavior)
    out = bound_dna(OwlDNA(curiosity=0.79), OwlDNA(curiosity=0.99), OwlDNA())
    assert abs(out.curiosity - 0.8) < 1e-9  # capped at 0.8 (neutral envelope hi)
```

- [ ] **Step 2: Run, verify FAIL** (`bound_dna` takes 2 args).

- [ ] **Step 3: Update `bound_dna`** (`dna_governor.py`)

```python
def bound_dna(current: OwlDNA, proposed: OwlDNA, anchor: OwlDNA) -> OwlDNA:
    """Rate-cap + clamp into the per-owl envelope [anchor±ENVELOPE] + author-deferring floor."""
    updates: dict[str, float] = {}
    for trait in _MUTABLE_TRAITS:
        cur = float(getattr(current, trait))
        prop = float(getattr(proposed, trait))
        anc = float(getattr(anchor, trait))
        delta = max(-MAX_DELTA, min(MAX_DELTA, prop - cur))          # rate cap (unchanged)
        lo, hi = max(0.0, anc - ENVELOPE), min(1.0, anc + ENVELOPE)  # envelope re-centered on anchor
        moved = max(lo, min(hi, cur + delta))
        if trait in FLOOR_TRAITS:
            moved = max(min(TRAIT_FLOOR, anc), moved)                # floor defers to author
        updates[trait] = moved
    return current.model_copy(update=updates)
```
Remove the now-unused `DNA_NEUTRAL` import if nothing else uses it (grep first — keep if still referenced).

- [ ] **Step 4: Update the existing 7 governor test calls** to pass a neutral anchor `OwlDNA()` as the third arg (these tests verify the envelope mechanics around neutral, which `anchor=OwlDNA()` preserves exactly). E.g. `bound_dna(OwlDNA(curiosity=0.50), OwlDNA(curiosity=0.95))` → `bound_dna(OwlDNA(curiosity=0.50), OwlDNA(curiosity=0.95), OwlDNA())`. Keep all their assertions.

- [ ] **Step 5: Update the production call site** (`evolution.py:289`)

```python
        anchor = await read_authored_dna(self._db, manifest.name) or OwlDNA()
        safe_dna = bound_dna(manifest.dna, new_dna, anchor)
```
Add `from stackowl.owls.dna_authored import read_authored_dna`. The `or OwlDNA()` falls back to a neutral anchor (logged-elsewhere) when no authored row exists, preserving today's behavior for un-captured owls.

- [ ] **Step 6: Run**

```
uv run pytest tests/owls/test_dna_governor.py tests/owls/test_evolution_feedback.py tests/journeys/test_persona_evolution_journey.py -v
```
Expected: ALL pass. The persona-evolution journeys (B/C) don't capture authored → `read_authored_dna` returns None → neutral anchor → identical envelope/floor math → they stay green. If a journey fails, STOP and report (it would mean the anchor math changed the neutral case — a bug).

- [ ] **Step 7: mypy + ruff; commit**

```bash
cd v2 && uv run mypy src/stackowl/owls/dna_governor.py src/stackowl/owls/evolution.py && uv run ruff check src/stackowl/owls/dna_governor.py src/stackowl/owls/evolution.py
cd /ssd/projects/stackowl-personal-ai-assistant
git add v2/src/stackowl/owls/dna_governor.py v2/src/stackowl/owls/evolution.py v2/tests/owls/test_dna_governor.py
git commit -m "feat(v2): authored-anchor evolution envelope + author-deferring floor — dna-completion C"
```

---

### Task 7: `DIRECTIVE_LATCH` — in-memory Schmitt hysteresis

**Files:**
- Create: `src/stackowl/owls/directive_latch.py`
- Test: `tests/owls/test_directive_latch.py` (create)

Mirror `FOCUS_TRACKER` (Lock, `_MAX_KEYS`, fail-safe, module singleton). Per `(owl, trait)`: `high_on`, `low_on`. Two direction-independent methods so `formality` (in both directive tables) latches each direction cleanly.

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_directive_latch.py
from stackowl.owls.directive_latch import (
    DirectiveLatch, HIGH_ENTER, HIGH_EXIT, LOW_ENTER, LOW_EXIT,
)
from stackowl.owls.evolution_limits import MAX_DELTA


def test_gap_exceeds_max_delta():
    # the invariant that makes the Schmitt actually stabilize: one batch can't cross the band
    assert (HIGH_ENTER - HIGH_EXIT) > MAX_DELTA
    assert (LOW_EXIT - LOW_ENTER) > MAX_DELTA


def test_high_lazy_seed_matches_plain_threshold():
    lt = DirectiveLatch()
    assert lt.high_state("o", "challenge_level", 0.72) is True   # >= enter → on
    assert lt.high_state("o2", "challenge_level", 0.50) is False  # below enter → off (cold)


def test_high_holds_in_deadband_then_exits():
    lt = DirectiveLatch()
    assert lt.high_state("o", "x", 0.72) is True    # enter
    assert lt.high_state("o", "x", 0.66) is True    # hold (between exit 0.60 and enter 0.70)
    assert lt.high_state("o", "x", 0.59) is False   # exit (< 0.60)
    assert lt.high_state("o", "x", 0.66) is False   # stays off in deadband (was off)


def test_low_direction_independent():
    lt = DirectiveLatch()
    assert lt.low_state("o", "formality", 0.28) is True   # enter low
    assert lt.low_state("o", "formality", 0.36) is True   # hold
    assert lt.low_state("o", "formality", 0.41) is False  # exit (> 0.40)


def test_reset_owl_clears():
    lt = DirectiveLatch()
    lt.high_state("o", "x", 0.72)
    lt.reset_owl("o")
    # after reset, next call cold-seeds from the value (0.66 < enter → off, not the held True)
    assert lt.high_state("o", "x", 0.66) is False


def test_singleton_exists():
    from stackowl.owls.directive_latch import DIRECTIVE_LATCH
    DIRECTIVE_LATCH.reset_owl("nobody")  # no crash
```

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement `src/stackowl/owls/directive_latch.py`**

```python
"""In-memory Schmitt-trigger hysteresis for DNA->prompt directives. Per (owl, trait, direction)
latch: once a HIGH directive turns on at >=0.70 it stays on until the trait drops <0.60 (symmetric
LOW at <=0.30 / >0.40). The gap (0.10 = 2*MAX_DELTA) ensures one evolution batch can't cross it.
In-memory (DNA changes per-batch, not per-turn; cold-start re-seeds from value); fail-OPEN (any
error -> plain threshold). Module-singleton, mirrors FOCUS_TRACKER."""
from __future__ import annotations

from threading import Lock

from stackowl.infra.observability import log

HIGH_ENTER = 0.70
HIGH_EXIT = 0.60
LOW_ENTER = 0.30
LOW_EXIT = 0.40
_MAX_KEYS = 512


class DirectiveLatch:
    def __init__(self) -> None:
        # (owl, trait) -> [high_on, low_on]
        self._by_key: dict[tuple[str, str], list[bool]] = {}
        self._lock = Lock()

    def _entry(self, owl: str, trait: str) -> list[bool] | None:
        key = (owl, trait)
        e = self._by_key.get(key)
        if e is None:
            if len(self._by_key) >= _MAX_KEYS:
                self._by_key.pop(next(iter(self._by_key)))
            e = [False, False]
            self._by_key[key] = e
            return None  # signal: not yet seeded
        return e

    def high_state(self, owl: str, trait: str, value: float) -> bool:
        try:
            with self._lock:
                e = self._entry(owl, trait)
                if e is None:  # lazy seed = plain threshold
                    seeded = value >= HIGH_ENTER
                    self._by_key[(owl, trait)][0] = seeded
                    return seeded
                if value >= HIGH_ENTER:
                    new = True
                elif value < HIGH_EXIT:
                    new = False
                else:
                    new = e[0]  # hold
                if new != e[0]:
                    log.engine.info("[owls] directive_latch.flip", extra={"_fields": {"owl": owl, "trait": trait, "dir": "high", "old": e[0], "new": new, "value": value}})
                e[0] = new
                return new
        except Exception as exc:  # fail-open: plain threshold
            log.engine.error("[owls] directive_latch.high_state failed", exc_info=exc, extra={"_fields": {"owl": owl, "trait": trait}})
            return value >= HIGH_ENTER

    def low_state(self, owl: str, trait: str, value: float) -> bool:
        try:
            with self._lock:
                e = self._entry(owl, trait)
                if e is None:
                    seeded = value <= LOW_ENTER
                    self._by_key[(owl, trait)][1] = seeded
                    return seeded
                if value <= LOW_ENTER:
                    new = True
                elif value > LOW_EXIT:
                    new = False
                else:
                    new = e[1]
                if new != e[1]:
                    log.engine.info("[owls] directive_latch.flip", extra={"_fields": {"owl": owl, "trait": trait, "dir": "low", "old": e[1], "new": new, "value": value}})
                e[1] = new
                return new
        except Exception as exc:
            log.engine.error("[owls] directive_latch.low_state failed", exc_info=exc, extra={"_fields": {"owl": owl, "trait": trait}})
            return value <= LOW_ENTER

    def reset_owl(self, owl: str) -> None:
        try:
            with self._lock:
                for key in [k for k in self._by_key if k[0] == owl]:
                    del self._by_key[key]
        except Exception as exc:
            log.engine.error("[owls] directive_latch.reset_owl failed", exc_info=exc, extra={"_fields": {"owl": owl}})

    def clear_all(self) -> None:
        with self._lock:
            self._by_key.clear()


DIRECTIVE_LATCH = DirectiveLatch()
```

> Note the seed path writes through `self._by_key[(owl,trait)]` because `_entry` already inserted the `[False,False]` list before returning `None`. Confirm that indexing is correct (it is — `_entry` inserts then returns None on first call).

- [ ] **Step 4: Run, verify PASS** (6 tests).

- [ ] **Step 5: mypy + ruff; commit**

```bash
cd v2 && uv run mypy src/stackowl/owls/directive_latch.py && uv run ruff check src/stackowl/owls/directive_latch.py
cd /ssd/projects/stackowl-personal-ai-assistant
git add v2/src/stackowl/owls/directive_latch.py v2/tests/owls/test_directive_latch.py
git commit -m "feat(v2): DIRECTIVE_LATCH in-memory Schmitt hysteresis (fail-open, audited) — dna-completion C"
```

---

### Task 8: Wire the latch into `DNAPromptInjector`

**Files:**
- Modify: `src/stackowl/owls/dna_injector.py` (`inject` uses the latch instead of raw thresholds)
- Test: `tests/owls/test_dna_injector.py` (create — the injector is currently untested in isolation)

- [ ] **Step 1: Write the failing test**

```python
# tests/owls/test_dna_injector.py
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.owls.directive_latch import DIRECTIVE_LATCH
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest


def _m(name="scout"):
    return OwlAgentManifest(name=name, role="r", system_prompt="BASE", model_tier="fast")


def test_high_directive_emitted_above_enter():
    DIRECTIVE_LATCH.clear_all()
    out = DNAPromptInjector().inject(_m(), OwlDNA(challenge_level=0.72))
    assert "BASE" in out and out != "BASE"  # a directive was appended


def test_directive_latches_through_deadband():
    DIRECTIVE_LATCH.clear_all()
    inj = DNAPromptInjector()
    m = _m("o1")
    on = inj.inject(m, OwlDNA(challenge_level=0.72))      # enter HIGH
    hold = inj.inject(m, OwlDNA(challenge_level=0.66))    # deadband → still on
    off = inj.inject(m, OwlDNA(challenge_level=0.58))     # exit
    assert on != "BASE" and hold != "BASE" and off == "BASE"


def test_no_directive_in_neutral():
    DIRECTIVE_LATCH.clear_all()
    out = DNAPromptInjector().inject(_m("o2"), OwlDNA())  # all 0.5
    assert out == "BASE"
```

- [ ] **Step 2: Run, verify FAIL** (injector still uses raw thresholds → the deadband-hold test fails: at 0.66 the old code emits nothing).

- [ ] **Step 3: Update `inject`** (`dna_injector.py`) — replace the two threshold comparisons with the latch (keyed on `manifest.name`):

```python
    def inject(self, manifest: OwlAgentManifest, dna: OwlDNA) -> str:
        from stackowl.owls.directive_latch import DIRECTIVE_LATCH
        directives: list[str] = []
        for trait, directive in _HIGH_DIRECTIVES:
            value = float(getattr(dna, trait))
            if DIRECTIVE_LATCH.high_state(manifest.name, trait, value):
                directives.append(directive)
        for trait, directive in _LOW_DIRECTIVES:
            value = float(getattr(dna, trait))
            if DIRECTIVE_LATCH.low_state(manifest.name, trait, value):
                directives.append(directive)
        if not directives:
            return manifest.system_prompt
        joined = "\n- ".join(directives)
        return f"{manifest.system_prompt}\n\nBehavioural modulation (from owl DNA):\n- {joined}"
```

(Keep the rest of the method/file. The `_HIGH_THRESHOLD`/`_LOW_THRESHOLD` constants are now unused — leave them or remove; the latch owns the thresholds via `HIGH_ENTER`/`LOW_ENTER`. The `assemble.py` call site `inject(manifest, manifest.dna)` is unchanged — no assemble edit.)

- [ ] **Step 4: Run**

```
uv run pytest tests/owls/test_dna_injector.py tests/journeys/test_persona_evolution_journey.py -v
```
Expected: new tests pass; the persona journey's injector assertions still pass (a trait pushed firmly above 0.70 still emits; lazy-seed makes cold-start match the old binary). If the journey asserted behavior exactly at a hold-zone value, adjust the journey's value to be unambiguous (above enter or below exit) WITHOUT weakening intent — but first confirm whether it actually breaks.

- [ ] **Step 5: mypy + ruff; commit**

```bash
cd v2 && uv run mypy src/stackowl/owls/dna_injector.py && uv run ruff check src/stackowl/owls/dna_injector.py
cd /ssd/projects/stackowl-personal-ai-assistant
git add v2/src/stackowl/owls/dna_injector.py v2/tests/owls/test_dna_injector.py
git commit -m "feat(v2): DNA directive injector uses Schmitt latch (anti-flicker) — dna-completion C"
```

---

### Task 9: `/owls reset-dna` + current-vs-authored `/owls dna`

**Files:**
- Modify: `src/stackowl/commands/owls_command.py` (`handle` dispatch, `_USAGE`, new `_reset_dna`, extend `_dna`)
- Test: `tests/commands/test_owls_reset_dna.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/commands/test_owls_reset_dna.py
import pytest
from stackowl.commands.owls_command import OwlsCommand
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_authored import capture_one_authored
from stackowl.owls.dna_storage import upsert_owl_dna
from stackowl.owls.directive_latch import DIRECTIVE_LATCH


def _cmd(tmp_db):
    reg = OwlRegistry()
    reg.register(OwlAgentManifest(name="scout", role="r", system_prompt="p", model_tier="fast",
                                  dna=OwlDNA(challenge_level=0.5)), source_name="t")
    return OwlsCommand(owl_registry=reg, db=tmp_db, event_bus=None, tool_registry=None), reg


@pytest.mark.asyncio
async def test_reset_dna_requires_confirm(tmp_db):
    cmd, _ = _cmd(tmp_db)
    out = await cmd.handle("reset-dna scout")
    assert "YES" in out  # confirmation prompt, no action


@pytest.mark.asyncio
async def test_reset_dna_reverts_to_authored_and_live_refreshes(tmp_db):
    cmd, reg = _cmd(tmp_db)
    await capture_one_authored(tmp_db, "scout", OwlDNA(challenge_level=0.5))   # authored
    await upsert_owl_dna(tmp_db, "scout", OwlDNA(challenge_level=0.8), table="owl_dna")  # evolved
    from stackowl.owls.dna_hydrator import apply_dna_overlay
    apply_dna_overlay(reg, "scout", OwlDNA(challenge_level=0.8))               # live = evolved
    out = await cmd.handle("reset-dna scout YES")
    assert "reset" in out.lower()
    assert reg.get("scout").dna.challenge_level == 0.5   # live registry refreshed to authored
    rows = await tmp_db.fetch_all("SELECT challenge_level FROM owl_dna WHERE owl_name = ?", ("scout",))
    assert rows[0]["challenge_level"] == 0.5             # evolved store reset to authored


@pytest.mark.asyncio
async def test_reset_dna_no_authored_baseline(tmp_db):
    cmd, _ = _cmd(tmp_db)
    out = await cmd.handle("reset-dna scout YES")
    assert "no authored" in out.lower()


@pytest.mark.asyncio
async def test_reset_dna_clears_latch(tmp_db):
    cmd, reg = _cmd(tmp_db)
    await capture_one_authored(tmp_db, "scout", OwlDNA(challenge_level=0.5))
    DIRECTIVE_LATCH.high_state("scout", "challenge_level", 0.72)  # latch ON
    await cmd.handle("reset-dna scout YES")
    # after reset the latch is cleared → cold-seed at 0.66 (deadband) → off
    assert DIRECTIVE_LATCH.high_state("scout", "challenge_level", 0.66) is False
```

- [ ] **Step 2: Run, verify FAIL** (`reset-dna` unknown → `_USAGE`).

- [ ] **Step 3: Add the dispatch branch** in `handle` (after the `_dna` branch):

```python
        elif sub == "reset-dna":
            result = await self._reset_dna(rest)
```
Extend `_USAGE` to document `reset-dna <name> YES`.

- [ ] **Step 4: Implement `_reset_dna`** (mirror `_remove`'s confirm; reuse `read_authored_dna`/`upsert_owl_dna`/`apply_dna_overlay`/`DIRECTIVE_LATCH`)

```python
    async def _reset_dna(self, rest: str) -> str:
        if self._registry is None:
            return _NO_REGISTRY
        tokens = rest.split()
        if not tokens:
            return "Usage: /owls reset-dna <name> YES"
        name = tokens[0]
        manifest = self._registry.get(name)  # raises OwlNotFoundError → handled by handle()
        confirmed = len(tokens) > 1 and tokens[1] == "YES"
        if not confirmed:
            return (f"⚠ This reverts owl '{name}' DNA to its authored baseline (evolution discarded).\n"
                    f"   Type: /owls reset-dna {name} YES to confirm.")
        if self._db is None:
            return "DNA store unavailable."
        from stackowl.owls.dna_authored import read_authored_dna
        from stackowl.owls.dna_storage import upsert_owl_dna
        from stackowl.owls.dna_hydrator import apply_dna_overlay
        from stackowl.owls.directive_latch import DIRECTIVE_LATCH
        authored = await read_authored_dna(self._db, name)
        if authored is None:
            return f"No authored baseline recorded for '{name}' — nothing to reset to."
        await upsert_owl_dna(self._db, name, authored, table="owl_dna")  # evolved → authored
        apply_dna_overlay(self._registry, name, authored)               # live refresh
        DIRECTIVE_LATCH.reset_owl(name)                                 # clear latch
        return f"✓ Owl '{name}' DNA reset to authored baseline."
```

- [ ] **Step 5: Extend `_dna`** to show current-vs-authored. After loading `manifest` and the `db_row`, also read authored and include it in the readout:

```python
        authored = None
        if self._db is not None:
            from stackowl.owls.dna_authored import read_authored_dna
            authored = await read_authored_dna(self._db, name)
        result = format_dna_display(name, manifest.dna, db_row, authored=authored)
```
Extend `format_dna_display` (in `owls_helpers.py`) to accept `authored: OwlDNA | None = None` and, when present, render a `current | authored` column per trait (a small additive change — keep the existing single-column output when `authored is None`). Add the param with a default so existing callers/tests are unaffected.

> Confirm `_NO_REGISTRY` is the real constant name used by other handlers (recon shows the guard pattern). Confirm `format_dna_display`'s real signature in `owls_helpers.py` and extend it minimally.

- [ ] **Step 6: Run**

```
uv run pytest tests/commands/test_owls_reset_dna.py tests/commands/test_owls_parse.py tests/test_owls_command_registration.py -v
```
Expected: PASS (new + existing command tests).

- [ ] **Step 7: mypy + ruff; commit**

```bash
cd v2 && uv run mypy src/stackowl/commands/owls_command.py src/stackowl/commands/owls_helpers.py && uv run ruff check src/stackowl/commands/owls_command.py src/stackowl/commands/owls_helpers.py
cd /ssd/projects/stackowl-personal-ai-assistant
git add v2/src/stackowl/commands/owls_command.py v2/src/stackowl/commands/owls_helpers.py v2/tests/commands/test_owls_reset_dna.py
git commit -m "feat(v2): /owls reset-dna (revert->authored, live refresh, clear latch) + current-vs-authored readout — dna-completion C"
```

---

### Task 10: Gateway journey — the full loop

**Files:**
- Create: `tests/journeys/test_dna_completion_journey.py` (mirror `test_persona_evolution_journey.py` scaffolding)

- [ ] **Step 1: Write the journey** (mock only the AI provider; distinct boundary-adjacent values; the real `EvolutionCoordinator` + governor + DB + registry)

Recon the template `tests/journeys/test_persona_evolution_journey.py` (its `db` fixture, `_manifest`, `_seed_messages`, `_run_batch`, `_persisted_dna`, MockProvider). Then assert the loop:
```python
# Pseudocode for the journey body — implement against the template's real helpers.
# 1. Register an owl authored challenge_level=0.75. capture_authored_dna(reg, db).
#    Assert read_authored_dna(db, name).challenge_level == 0.75.
# 2. Seed the owned-owl's owl_dna current=0.74; run an evolution batch proposing a big +move.
#    Assert the evolved value is clamped inside the ANCHORED band [0.45,1.0] (e.g. ~0.79),
#    NOT the old neutral cap 0.8. (Distinguish: pick numbers where neutral [0.2,0.8] and
#    anchored [0.45,1.0] give different results.)
# 3. DIRECTIVE_LATCH: push challenge_level to 0.72 → inject emits the high directive;
#    drop to 0.66 (deadband) → STILL emitted (latched); drop to 0.58 → no longer emitted.
# 4. /owls reset-dna <name> YES → assert: owl_dna == authored (0.75), live registry manifest
#    .dna.challenge_level == 0.75 (apply_dna_overlay), latch cleared (a subsequent inject at
#    0.66 emits nothing — cold-seed off), and a directive recomputes fresh.
```

- [ ] **Step 2: Run, iterate the HARNESS to GREEN.** If a journey step exposes a REAL feature bug (not harness), STOP and report — do not weaken assertions. `DIRECTIVE_LATCH.clear_all()` in setup so latch state doesn't leak across tests.

- [ ] **Step 3: Full targeted regression**

```
uv run pytest tests/owls/ tests/commands/test_owls_reset_dna.py tests/startup/test_authored_capture_wiring.py tests/journeys/test_persona_evolution_journey.py tests/journeys/test_dna_completion_journey.py -v
```
Expected: all PASS.

- [ ] **Step 4: ruff; commit**

```bash
cd v2 && uv run ruff check tests/journeys/test_dna_completion_journey.py
cd /ssd/projects/stackowl-personal-ai-assistant
git add v2/tests/journeys/test_dna_completion_journey.py
git commit -m "test(v2): DNA-completion gateway journey (anchor envelope, latch, reset) — dna-completion C"
```

---

## Self-Review (against the spec)

**Spec coverage:**
- §2 architecture (DnaDefaults / authored store / governor anchor / latch / reset / shared upsert): Tasks 1-2 / 3-5 / 6 / 7-8 / 9 / 3.
- §3 DnaDefaults + no-op proof: Tasks 1-2 (equality-in-order incl. SQL column parse + frozen-literal guard; distinct-value round-trip in Task 3).
- §4 authored store (migration, capture coerced/skip-clobber/idempotent/orphan, boot-before-hydrate, creation seams): Tasks 3-5.
- §5 authored-anchor envelope + floor=min(TRAIT_FLOOR, anchor) + pure governor + caller fetches anchor: Task 6.
- §6 Schmitt (enter/exit, gap>MAX_DELTA, lazy-seed, hold-zone, audit flip, fail-open, reset_owl, per-direction for formality): Tasks 7-8.
- §7 reset-dna (YES confirm, →authored, live refresh, clear latch, keep checkpoints) + current-vs-authored readout: Task 9.
- §8 testing incl. the gateway journey: every task is TDD; Task 10 is the full-loop journey.
- §9 cuts: persisted latch (in-memory only — Task 7), graded directives, decay — none implemented.

**Placeholder scan:** no TBD/TODO. Concrete constants + real SQL. Codebase-binding points (real `_NO_REGISTRY`, `format_dna_display` signature, `svc.db_pool`, `_DNA_FIELDS` usage shape, the 7 governor test calls, the journey template helpers) are flagged inline with where to confirm.

**Type consistency:** `upsert_owl_dna(db, name, dna, *, table)` consistent (Tasks 3/5/9). `read_authored_dna(db, name)->OwlDNA|None`, `capture_one_authored(db, name, dna)`, `capture_authored_dna(registry, db)` consistent (4/5/6/9). `bound_dna(current, proposed, anchor)` consistent (6, evolution call). `DIRECTIVE_LATCH.high_state/low_state/reset_owl/clear_all` consistent (7/8/9). `TRAIT_NAMES`/`NEUTRAL` consistent (1/2/3/4). Migration `0051` only (latch is in-memory).

**Known codebase-binding risks (flagged, not gaps):** `format_dna_display` real signature; `_NO_REGISTRY` constant name; `StepServices.db_pool`; `_DNA_FIELDS` exact type/usage when repointed; whether removing `_UPSERT_DNA_SQL`/`DNA_NEUTRAL` imports leaves a dangling reference (grep before delete); the persona-evolution journey's exact injector-assertion values (confirm they're not at a hold-zone boundary). Each names where to confirm.

---

## Phase-2 Backlog (tracked)
| Item | Why deferred | Revisit |
|---|---|---|
| Persisted Schmitt latch | DNA moves per-batch not per-turn; in-memory kills all within-session flicker | cut (user-approved) |
| Graded directives (mild/strong) | LLM collapses them; 3-zone latch already gives off/hold/on | Phase-2 |
| `decay_rate_per_week` consumer | no reader exists today | separate story |
| Rich evolution-history UI | `/owls dna` current-vs-authored is the v1 slice | Phase-2 |
| Floor-as-absolute-safety-rail | user chose author-intent-wins | revisit on a bad-authoring incident |
