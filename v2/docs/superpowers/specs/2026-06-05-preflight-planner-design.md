# Epic 2 Story 3 — The Preflight Planner (E2-S3)

> Populates the `task_envelope` slot E2-S2 left inert: a goal-derived **least-privilege**
> tool set computed once, before a durable task's ReAct loop. The envelope is a *default*
> with an *audit trail*, not a hard boundary — it self-widens within the owl's bounds on
> demand (threat model: accidental **drift**, not an adversarial owl).

**Status:** Design approved (brainstorming forks resolved, 2026-06-05); pending party-mode hardening
**Builds on:** E2-S1 (`BoundsSpec`), E2-S2 (effective = `owl ∩ creation_ceiling ∩ task_envelope`, the inert `task_envelope` slot, the dispatch seam, `assert_task_narrowing_enforceable`)
**Followed by:** E2-S4 authorizer / budget governor; Epic 3 fs/network enforcement + FR35

---

## 1. Problem

E2-S2 shipped the *mechanism* (`task_envelope` slot + composition + soft-vs-hard distinction
deferred) but left the slot always `None`. The real root problem Dr. Quinn named — **drift**:
a wandering ReAct loop, or a confused owl, reaching for a tool the *goal* never required — is
unaddressed. S3 supplies **least-privilege-per-task**: compute the minimal tool set a goal
needs and make it the task's default, so off-goal tools are hidden by default and their use is
audited.

**Threat model (chosen): accidental drift, not adversarial.** The hard security boundary stays
`owl ∩ creation_ceiling` (S1/S2). The `task_envelope` is a *softer* inner default that reduces
blast radius for well-behaved tasks and produces an audit signal when a task reaches beyond its
plan. It is explicitly **not** a defense against a compromised/prompt-injected owl (such an owl
can `tool_search` and self-widen up to its owl bounds). We frame it honestly as such everywhere.

---

## 2. Approved design decisions (brainstorming forks)

| Fork | Decision |
|---|---|
| Derivation | **Hybrid** — LLM proposer (fast tier) + embedding floor + mandatory base/discovery union |
| Envelope strength | **Soft** — self-widen within owl bounds on an envelope-only block; never hard-blocks |
| Envelope value | least-privilege **default + drift audit trail**, AND **bias presentation** toward the plan |
| Planner failure | **Fail-open, loud** → `task_envelope=None` → S2 behavior (`owl ∩ ceiling`) |
| Gating | **Durable depth-0 tasks only** (long-running → highest drift risk; cost amortized) |
| Persistence | **None** — resume re-runs the full pipeline → the `plan` step re-plans automatically |
| Child propagation | **None** — children stay clamped by `creation_ceiling`; per-child planning is future work |

---

## 3. Architecture

### 3.1 New pipeline step: `plan` (between `assemble` and `execute`)

`src/stackowl/pipeline/steps/plan.py`. Registered in `PIPELINE_STEPS` between `assemble` and
`execute`. Signature `async def run(state) -> PipelineState`. **Gate** (no-op pass-through unless
all hold):
- `state.task_id is not None` (durable task), and
- `state.delegation_depth == 0` (not a delegated child / sub-pipeline), and
- a `tool_registry` with tools AND a `provider_registry` are wired in services.

When gated out, returns `state` unchanged (`task_envelope` stays `None` → S2 behavior). On a
durable resume the gate is satisfied again, so the step **re-plans** (acceptable: soft envelope,
non-binding; a different plan just re-audits differently). Entry/exit logged (4-point).

### 3.2 The planner (`src/stackowl/pipeline/planner/`)

`PreflightPlanner` composes two independently-failing strategies + a mandatory floor:

- **`ToolProposer` (LLM, fast tier).** Builds a prompt from the goal (`state.input_text`) + the
  catalog (`[(name, description)]` from `tool_registry.all()`), asks a fast-tier provider
  (`provider_registry.get_with_cascade("fast")`) for a minimal tool-name list via a structured
  response (a Pydantic `ToolSelection` model; permissive parse with a marker fallback, mirroring
  the parliament synthesis parser). **Validates** every returned name against the real catalog —
  hallucinated names are dropped. Raises/returns empty on provider error.
- **`EmbeddingFloor` (best-effort).** If an embedder is available (`services.embedding_registry`),
  embeds the goal and each tool description, adds tools with cosine similarity ≥ a threshold — a
  safety margin so a too-narrow LLM pick doesn't strand the task. No embedder → returns empty set
  (skipped). Never raises to the caller (best-effort).
- **Mandatory base/discovery.** The union always includes the discovery meta-tools
  (`tool_search`, `tool_describe`) so the owl can always escape a too-narrow plan.

Result: `tools = validate(proposer) ∪ embedding_floor ∪ MANDATORY`. The planner builds
`candidate = BoundsSpec(tools=frozenset(tools))`, calls
`assert_task_narrowing_enforceable(owl_bounds, candidate)` (tools-only → passes; a guard against
a future planner emitting a non-tools axis), and returns it.

**Fail-open contract (loud):** if the proposer errors AND the floor is empty (or the whole
planner raises), the planner returns `None` and the `plan` step logs a WARNING; `task_envelope`
stays `None`. Availability is preserved (owl ∩ ceiling still enforce). A planner that returns a
*non-empty but over-narrow* set is fine — soft-widen + the mandatory discovery tools recover it.

### 3.3 Authorization: 3-way provenance + soft-grant at the seam

S2's seam computes `denied_by ∈ {owl, task}`, where `task` conflates **ceiling** and **envelope**.
S3 refines this to **three** sources so the soft rule applies to the envelope ONLY:

At `execute._dispatch`, when `check_effective_bounds(effective, name)` blocks, classify by
re-checking against progressively-narrower specs:
- `owl`-only permits? If **no** → `owl` block → **hard deny** (unchanged).
- `owl ∩ ceiling` permits? If **no** (but owl alone does) → `ceiling` block → **hard deny** (the
  TOCTOU guard must never self-widen).
- else the block is from the **envelope** → **soft**: log a `[authz] drift: tool off-plan —
  granting within owl bounds` WARNING (the audit signal), add `name` to a per-run `soft_granted`
  set, and **proceed to execution** (do NOT return the block).

`soft_granted` is checked at the top of the envelope-classification so a re-called off-plan tool
short-circuits to granted (no repeated audit spam, symmetric with `denied_this_run`). The
hard-deny paths (`owl`, `ceiling`) are byte-for-byte S2. Provenance is computed only on the block
branch (not the hot allow path), reusing the guarded-recompute pattern from S2.

### 3.4 Presentation bias: `restrict_to`

The presented tool schema (what the model SEES) is narrowed toward the plan so the owl rarely
drifts in the first place. The presentation machinery is currently additive-union-with-cap with a
*non-evictable* base set (`shell`, `write_file`, …) — so pinning alone can't hide drift-prone
tools. Add one **additive, backward-compatible** parameter:

- `ToolRegistry.to_provider_schema(..., restrict_to: frozenset[str] | None = None)` →
  `ToolPresentation.select(..., restrict_to=...)`.
- When `restrict_to` is `None` (every call today): **unchanged**.
- When set: the presented set = `always_present (tool_search/tool_describe) ∪ (restrict_to ∩ catalog)`,
  capped. The broad base set and profile groups are dropped *for that turn* — but `always_present`
  (discovery) is retained as the escape hatch.

In `execute._run_with_tools`, when `state.task_envelope is not None`, pass
`restrict_to=state.task_envelope.tools` to `to_provider_schema`. Off-plan tools are hidden;
the owl `tool_search`es to surface one → it hydrates → next turn it's presented → the owl calls
it → §3.3 soft-grants + audits. Closed loop: hidden-by-default, discoverable, granted-with-audit.

---

## 4. Data flow

```
plan step (durable, depth 0, tools+provider present)
  proposer(goal, catalog) ─┐
  embedding_floor(goal) ───┼─► tools ∪ MANDATORY(discovery)
                           ┘     │ assert_task_narrowing_enforceable(owl, candidate)
                                 ▼
  state.evolve(task_envelope = BoundsSpec(tools=...))   [fail → None, logged]
        │
        ▼
execute._run_with_tools
  presentation: to_provider_schema(restrict_to = task_envelope.tools)  ── owl SEES plan ∪ discovery
  _dispatch(name):
     effective = owl ∩ ceiling ∩ envelope            (compute_effective_bounds)
     block? → classify: owl|ceiling = HARD deny ; envelope = SOFT grant + drift-audit + proceed
        │
Kill ─▶ Resume → full pipeline re-runs → plan step RE-PLANS (no persistence)
```

---

## 5. Error handling / invariants

| Concern | Resolution |
|---|---|
| Planner LLM error / no provider | proposer empty; if floor also empty → `task_envelope=None`, WARNING (fail-open) |
| Embedder absent (ARM/Jetson) | floor returns empty, skipped — LLM-only; logged debug |
| Hallucinated tool names | validated against `tool_registry` catalog; dropped |
| Over-narrow plan strands a task | soft-widen + mandatory `tool_search`/`tool_describe` always present → recoverable |
| Ceiling must never self-widen | 3-way provenance: only `envelope` blocks are soft; `ceiling`/`owl` stay hard |
| Non-durable / interactive turns | gated out → `task_envelope=None` → byte-for-byte S2 |
| Delegated children | gated out (`depth>0`); clamped by `creation_ceiling` as in S2 |
| Presentation back-compat | `restrict_to=None` default → every existing call unchanged |
| Planner emits a non-tools axis | `assert_task_narrowing_enforceable` raises in the planner → caught → fail-open |
| Audit not silent | every soft-grant logs a WARNING with tool + trace_id (the drift signal) |

---

## 6. Testing (TDD; only the AI provider / embedder mocked)

**Planner units (`tests/pipeline/planner/`)**
- `ToolProposer`: parses a structured response; **drops hallucinated names**; provider error → empty.
- `EmbeddingFloor`: adds above-threshold tools; no embedder → empty; never raises.
- `PreflightPlanner`: union includes mandatory discovery; both strategies fail → `None`;
  honesty-guard rejection → `None`; a valid plan → `BoundsSpec(tools=...)`.

**Plan step (`tests/pipeline/steps/`)** — gating truth table: durable+depth0+tools+provider → sets
envelope; non-durable / depth>0 / no tools / no provider → pass-through `None`; planner failure →
`None` + WARNING.

**Seam (`tests/authz/` / dispatch)** — 3-way provenance: `owl` block hard-denies; `ceiling` block
hard-denies (resumed-widened-owl case from S2 still hard); `envelope`-only block **soft-grants +
audits + executes**; a re-called soft-granted tool short-circuits; the S2 hard-deny tests unchanged.

**Presentation (`tests/tools/`)** — `restrict_to=None` → identical to today; `restrict_to={A}` →
presented set is `{A} ∪ {tool_search, tool_describe}`, base/groups dropped, cap respected.

**Gateway journey (`tests/journeys/`)** — a durable goal needing only tool A: the plan narrows to A;
B is hidden from the schema; the scripted owl `tool_search`es + calls B → B is **soft-granted +
audited** (not blocked) and the task completes; assert the drift-audit log fired and A ran.

---

## 7. Out of scope (tracked)

| Item | Why | Revisit |
|---|---|---|
| Per-child (delegated) planning | depth>0 gated out; creation_ceiling already clamps | later |
| Persisting the plan across resume | resume re-plans; soft envelope needs no stable plan | only if planning cost becomes painful |
| Adversarial-grade enforcement | this is drift defense by design | E2-S4 authorizer |
| fs/network/data envelope axes | unenforced; honesty guard refuses narrowing them | Epic 3 |
| Caching/precomputing tool-description embeddings | optimization | if floor latency matters |

---

## 8. Placement

- `src/stackowl/pipeline/steps/plan.py` — the step (dictated by pipeline structure).
- `src/stackowl/pipeline/planner/{__init__,planner,proposer,floor}.py` — planner package
  (pipeline layer: reads provider/embedder/tool catalog; authz stays pure).
- `src/stackowl/tools/registry.py` + `src/stackowl/tools/_infra/presentation.py` — the additive
  `restrict_to` parameter.
- `src/stackowl/pipeline/steps/execute.py` — 3-way provenance + soft-grant; `restrict_to` wiring.
- `src/stackowl/pipeline/registry.py` — register the `plan` step.
