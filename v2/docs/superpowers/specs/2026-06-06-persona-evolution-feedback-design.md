# Persona-Evolution Feedback Loop + Memory Gaps (Owl Capability Arc, Story 4 — final)

> The owl's "evolving personality" is dead in practice: `EvolutionCoordinator` mutates DNA
> after conversations and writes it to the `owl_dna` table, but **nothing reads it back** —
> the live persona always uses the boot-default DNA. This wires the feedback loop **safely**
> (a slow-poison-resistant clamp/cap/envelope/floor governor), **live** (applied at the next
> turn boundary, never mid-turn), and **persistently** (survives restart via boot hydration).
> Plus two small memory gaps. Reshaped from a maximal draft by party-mode (Winston/Murat/Dr.
> Quinn/Amelia).

**Status:** Design approved (2026-06-06); pending spec re-review
**Builds on:** `owls/evolution.py::EvolutionCoordinator`, `owls/dna_injector.py::DNAPromptInjector`, `OwlRegistry.replace` (owl-builder S1 [[project_owl_builder_arc]]), `assemble.py` persona injection, the memory tri-store + `FactPromoter`/DreamWorker.
**Recon finding:** the prior memory/persona diagnoses ([[project_memory_persona_bugs]], [[project_memory_still_broken_2026_06_01]]) are **mostly FIXED** (persona injection, history threading, recall short-circuit, scheduler idempotency, force_promote-embed-at-entry, Telegram parity). The ONE genuinely-broken loop is DNA-evolution→persona feedback.

---

## 1. Problem & value

`EvolutionCoordinator` computes DNA deltas from conversation batches, mutates `new_dna`, persists via `_UPSERT_DNA_SQL` → the `owl_dna` table — and stops. `OwlRegistry.get` returns the in-memory `self._owls[name]` (a frozen `OwlAgentManifest` whose `.dna` is the YAML boot-default). No code hydrates `owl_dna` back into the registry; no reader exists outside a display command. So **evolution is write-only** — `assemble.py`'s `DNAPromptInjector.inject(manifest, manifest.dna)` always sees boot-default DNA. The owl never actually evolves. This is the last gap in the Owl Capability arc.

**Value:** make the evolving-personality premise real — *safely*. Wiring a conversation→personality→persisted→reloaded loop creates a **positive-feedback control system**; without a governor it is a **slow-poison surface** (a manipulative user or an injected document the owl processes can nudge the persona toward sycophancy/safety-softness one plausible step at a time, and persistence makes the damage durable). The safety governor is therefore not optional — it is the precondition for wiring the loop at all.

**Scope (user):** the feedback loop (live + persistent) + two small memory gaps. The fuzzy concerns (memory-promotion injected-content recall; a reset command) are deferred.

---

## 2. Decomposition — 4a then 4b (Winston)

| | Sub-story | Deliverable |
|---|---|---|
| **4a** | **DNA-evolution → persona feedback (safe)** | The DNA-safety governor (`_bound_dna`); the shared `PersonaRefresher` overlay primitive; boot `DnaHydrator`; `EvolutionCoordinator` persist-then-refresh + audit; Schmitt hysteresis in the injector. The owl evolves (bounded) and it reaches the live persona + survives restart. |
| **4b** | **Memory gaps** | `force_promote`/`_promote_one` embedding completeness (fail-open); a cross-session-recall e2e journey proving learn-in-A → recall-in-B. |

4a is the owls-package feedback loop; 4b is orthogonal memory-package hardening. Independent diffs/reviewers.

---

## 3. Story 4a — DNA-evolution → persona feedback (safe, live, persistent)

### 3.1 `_bound_dna` — the single DNA-safety governor (Murat P0-1 + Dr. Quinn)
A pure function, the ONE chokepoint for every DNA write (evolution-persist) AND defensively at hydration. Given the **authored-default** DNA (anchor), the **current** DNA (base for delta), and a **proposed** DNA, it returns a safe DNA per numeric trait:
1. **Clamp** to `[0, 1]` (NaN/inf/None/non-numeric → fall back to the authored/current value — NaN passes Pydantic `float`, so test `v != v` explicitly).
2. **Max-delta per batch**: `|proposed − current| ≤ MAX_DELTA` (default **0.05**) — no single conversation is behaviorally observable; only a sustained trend crosses a directive threshold.
3. **Envelope**: clamp to `authored_default ± ENVELOPE` (default **0.2**, capability-scaled — tighter on the weak box). The authored YAML persona is the gravitational center; evolution orbits, never roams `[0,1]`.
4. **Safety floors** on judgment traits: `challengeLevel` and `precision` may never drop below a floor (e.g. `0.25`) — evolution can tune the persona but can never fully disarm its willingness to push back / be precise.

Non-numeric traits (`learnedPreferences`, `expertiseGrowth` dicts) are passed through untouched (iterate `model_fields`, only govern numeric scalars; build via `model_copy(update=...)`). `MAX_DELTA`/`ENVELOPE`/floors are settings-driven (no magic literals). One validator, reused by both write sites.

### 3.2 `PersonaRefresher` — the shared DNA-only overlay primitive (Winston)
```python
class PersonaRefresher(Protocol):   # impl in owls/dna_hydrator.py (or owls/persona_refresh.py)
    def apply(self, owl_name: str, new_dna: OwlDNA) -> bool: ...
```
Impl: `current = registry.get(owl_name); registry.replace(current.model_copy(update={"dna": new_dna}))`. **DNA-only** — `model_copy(update={"dna": …})` touches only `.dna`; identity fields (name/role/system_prompt/tools/bounds) are structurally untouched (yaml-sourced). The SAME primitive is used by the boot hydrator and by `EvolutionCoordinator` (one overlay, two callers — no duplication). Injected as a Protocol so evolution depends on an interface, not the registry internals.

### 3.3 `DnaHydrator.hydrate_all()` — boot persistence (Murat P0-2)
At startup, **after `register_builtin_personas`, before serve**: read all `owl_dna` rows (via ONE canonical SELECT lifted into a store method, shared with `owls_command`), and for each owl **in the registry**: `_coerce_dna(authored=manifest.dna, row=traits)` → `PersonaRefresher.apply`. Fail-safe **per row** (try-wrapped): a corrupt/NaN/out-of-range/missing-trait row → keep authored DNA + **log loudly** (`log.engine.warning`); a row for an owl **not** in the registry → skip + warn (never auto-create a persona from a DNA row). One bad row never aborts the rest, never crashes boot, never injects NaN. `_coerce_dna` reuses `_bound_dna`'s clamp (defensive; the envelope/delta don't apply at boot — only the [0,1] clamp + default-fill).

### 3.4 `EvolutionCoordinator` — persist-then-refresh + audit
After computing `new_dna`: `safe = _bound_dna(authored_default, current, new_dna)` (the governor) → **persist `safe`** via the existing `_UPSERT_DNA_SQL` → then **refresh live** via `PersonaRefresher.apply(name, safe)` (re-fetch the *current* manifest inside `apply` so a concurrent owl-builder identity edit isn't clobbered). **Persist before refresh** (DB is source of truth; a crash between leaves durable correct + live self-heals next boot). **Audit (Murat P0-3):** log every batch delta `{owl, trait, old, new, delta, source: attribution|llm_fallback, batch_id, ts}` to the structured JSONL log (cheap; makes drift detectable + reversible). The coordinator gets the authored-default DNA (a `DnaDefaults` holder captured at `register_builtin_personas`) for the envelope anchor.

### 3.5 Live = next-turn boundary, never mid-turn (Dr. Quinn)
On a weak model the persona *is* the identity (rebuilt each turn). The refresh swaps the registry manifest; the **per-turn snapshot invariant** guarantees an in-flight turn never changes persona mid-stream: `assemble.run` reads `registry.get(state.owl_name)` exactly once per turn (verified in recon; tested here), so an in-flight turn finishes on the old DNA and the *next* turn sees the new. Evolution fires on batch boundaries (between conversations), so the refresh naturally lands at a conversation boundary. No extra session-gating needed — the snapshot + batch cadence suffice.

### 3.6 Schmitt hysteresis in `DNAPromptInjector` (Dr. Quinn)
The existing 0.3–0.7 deadband is good hysteresis but chatters at the threshold (a trait parked at 0.70 ± noise flips a directive on/off). Add split thresholds: a "high" directive turns **ON at ≥0.70, OFF only below 0.60**; "low" **ON at ≤0.30, OFF only above 0.40**. Because the injector is stateless per turn, model the hysteresis as a **widened deadband keyed off the directive's last state** — OR, simplest stateless equivalent given `_bound_dna` already caps the rate: keep the single-threshold injector but rely on the 0.05 max-delta + envelope to bound chatter, and add the split-threshold only if a test shows chatter. **Decision:** implement the split-threshold statelessly is impossible without prior state; since the persona is recomputed each turn from the trait value alone, true Schmitt needs the prior directive state. Given `_bound_dna`'s 0.05 cap already makes a single batch sub-threshold, **defer true Schmitt to Phase-2** and rely on max-delta + envelope for stability in 4a (documented). *(This is the one party recommendation we consciously scope down — see §7.)*

### 3.7 DNA is personality-only (not authz)
DNA traits shape the *persona prompt* (consent framing, verbosity, willingness to push back) — they are NOT the Epic-2 `BoundsSpec` (tools/fs/network/data/caps). Evolution can never change what the owl is *authorized* to do, only how it *behaves within* those bounds. No privilege-escalation surface. The safety floors (§3.1) protect the *judgment* the persona expresses, which is the only behavioral lever evolution touches.

---

## 4. Story 4b — Memory gaps

### 4.1 `force_promote` / `_promote_one` embedding completeness (Amelia)
`FactPromoter._promote_one` LanceDB-upserts only when `fact.embedding` is present; `force_promote` of a **miner-staged** fact (which lacks an embedding) lands FTS-only → semantic recall misses it. Fix at `_promote_one` (so the normal DreamWorker path self-heals too): `if fact.embedding is None: fact = fact.model_copy(update={"embedding": await self._best_effort_embed(fact.text)})` before the LanceDB branch — reuse `memory_helpers._best_effort_embed` (don't reimplement), wire `embedding_registry` into `FactPromoter`, **fail-open** (embed None/raises → FTS-only as today, no crash; guard `embedding is None` to avoid double-embed; `StagedFact` frozen → `model_copy`).

### 4.2 Cross-session-recall e2e journey (proof)
A gateway journey (mock ONLY the AI provider): session A `remember`s a fact → a **deterministic** DreamWorker promote pass (call the worker's promote phase directly, not the scheduler interval; inject a `Clock` past the settle window) → session B (fresh turn) recalls the fact (a deterministic fake embedder gives exact cosine recall). Proves capture→promote→recall closes end-to-end + regression-guards §4.1. Plus a parallel DNA loop journey: run an evolution batch (mocked deltas) → assert `registry.get(owl).dna` reflects the bounded delta live → construct a fresh registry + `hydrate_all` against the same DB → assert the evolved DNA is reproduced (simulated restart) and the injector emits it.

---

## 5. Security model (Murat — non-negotiable in 4a)
- **Slow-poison resistance:** `_bound_dna` = clamp (value) + max-delta (rate) + envelope (range) + floors (terminal state). All four, one chokepoint, on every write.
- **Fail-safe hydration:** corrupt/NaN/orphan rows → authored DNA + loud log; never crash, never inject NaN. Same validator as the write path.
- **Audit:** every delta logged (detectable + reversible drift).
- **No escalation:** DNA ≠ bounds (§3.7).
- **No mid-turn persona change:** per-turn snapshot invariant (§3.5).

---

## 6. Data flow

```
BOOT (4a):
  OwlRegistry.from_settings → register_builtin_personas  (authored DNA captured into DnaDefaults)
  DnaHydrator.hydrate_all():
     for owl in registry: row = owl_dna[owl]?  →  _coerce_dna(authored=manifest.dna, row)  (clamp, default-fill, fail-safe)
                                               →  PersonaRefresher.apply(owl, safe_dna)   (model_copy(dna) → registry.replace)
  → serve

EVOLUTION BATCH (4a):
  EvolutionCoordinator: deltas → new_dna
     safe = _bound_dna(authored=DnaDefaults[owl], current=registry.get(owl).dna, proposed=new_dna)   # clamp+delta+envelope+floors
     persist safe (owl_dna)                  # DB = source of truth
     PersonaRefresher.apply(owl, safe)       # live refresh (re-fetch current manifest)
     audit-log {owl, trait, old, new, delta, source, batch, ts}

TURN (unchanged seam, now sees evolved DNA):
  assemble.run: manifest = registry.get(owl)  (snapshot ONCE)  → DNAPromptInjector.inject(manifest, manifest.dna)

MEMORY (4b):
  _promote_one(fact): if fact.embedding is None → fact = model_copy(embedding=_best_effort_embed(text))  (fail-open)
                      → committed_facts + FTS + LanceDB  → recallable semantically
```

---

## 7. Out of scope / deferred (tracked)
| Item | Why | Where |
|---|---|---|
| True split-threshold (Schmitt) hysteresis in the injector | needs prior-directive state (the injector is stateless per turn); `_bound_dna`'s 0.05 max-delta + envelope already bound chatter | Phase-2 (add if a chatter test fails) |
| `/owls reset-dna <name>` remediation command | deleting the owl_dna row already reverts to authored default (documented); a command is convenience | Phase-2 |
| Memory-promotion injected-content governance (recall of injected source text re-activating instructions) | a memory-promotion-path concern, not DNA-evolution; same content, different pipe | separate story (flag to miner/promotion owner) |
| Writer-writer per-owl lock (evolution + owl-builder racing get→copy→replace) | last-writer-wins is acceptable (evolution batched/infrequent, owl-builder interactive); documented | Phase-2 (asyncio.Lock if it bites) |
| Capability-scaled envelope width (wider on capable hosts) | the envelope is settings-driven; auto-scaling via the capability probe is a refinement | Phase-2 |

---

## 8. Testing (TDD; mock only the AI provider)
**4a units** — `_bound_dna`: clamp [0,1]; NaN/inf/None → fallback (not 0); max-delta caps a big proposed move; envelope clamps to authored±E; floors hold on challengeLevel/precision; dict traits passed through untouched. `_coerce_dna`/hydrator: overlays DNA but **keeps identity fields** (name/system_prompt/tools unchanged — proves model_copy overlay, not reconstruction); corrupt row → authored DNA + no crash; orphan owl → skip (spy on replace). `PersonaRefresher.apply`: DNA-only swap via registry.replace. `EvolutionCoordinator`: persist-then-refresh ordering (call recorder); refresh uses the re-fetched current manifest; `safe` is the bounded DNA; audit log emitted. Per-turn snapshot invariant: `assemble.run` calls `registry.get` exactly once.

**4b units** — `_promote_one` embeds when `fact.embedding is None` (fake embedder → LanceDB path taken); embed-raises → FTS-only, no crash; no double-embed.

**Gateway journeys** — (A) cross-session recall: remember in A → deterministic DreamWorker promote → recall in B (fake embedder for exact cosine). (B) DNA loop: evolution batch → live `registry.get(owl).dna` changed (bounded) → fresh registry + `hydrate_all` reproduces it (simulated restart) → injector emits evolved DNA. (C, security) a batch of adversarial deltas (all pushing challengeLevel→0) → `_bound_dna` holds the floor + caps the per-batch move → persona never crosses into "no pushback" (proves the slow-poison governor end-to-end).
