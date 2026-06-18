# Agent `owl_build` Tool — Self-Extending Owl-Builder (Phase-2 Story A)

> An OWL (the model, mid-turn) can create/edit/retire **specialist owls** — the self-extension
> capability deferred from owl-builder S1 as "an escalation minefield." Built safely by mirroring
> the shipped `tool_build` self-extension pattern, with the honest conclusion from party-mode:
> **the bounds clamp is a no-op for an unbounded creator, so CONSENT is the real security
> boundary** — fail-closed off-TTY, toolset shown to the human, conservative-default-ceiling so
> even the root owl's creations start safe. Reshaped by party-mode (Winston/Murat/Dr. Quinn/Amelia).

**Status:** Design approved (2026-06-06); pending spec re-review
**Builds on:** owl-builder S1 ([[project_owl_builder_arc]] — `SpecialistOwlBuilder.build`, `PRESETS`, `OwlRegistry`, `_upsert_to_yaml`, `manifest_to_yaml_entry`); the shipped self-extension precedent `tools/meta/tool_build.py`; Epic-2 bounds (`child_floor`, `BoundsSpec.intersect`, `effective_bounds`); `ConsequentialActionGate`; the DNA-hydrator boot-pass precedent.
**Phase-2 arc:** A (this) → B skill-injection relevance-tiering → C DNA-evolution completion → D delegation hardening → E memory-promotion governance.

---

## 1. Problem, value, threat model

S1 shipped a **human** `/owls` builder; the **agent** tool was deferred because minting owls from inside a turn is an escalation surface. This delivers it — the deepest self-extension capability ([[feedback_agent_self_extends]], [[project_agentic_os_vision]]) — without opening that surface.

**Honest threat model (Murat — stated so nothing is oversold):**
- **In scope:** the **agent mid-turn, processing injected/untrusted content.** It can call tools but cannot patch code or write the filesystem outside its tool bounds. *Every mitigation targets this actor.*
- **Out of scope:** an **offline `stackowl.yaml` tamperer.** Single-user, self-hosted, the human owns the FS, apikeys are already plaintext config — editing the yaml ≈ editing the code (already-lost). yaml-revalidate-on-load buys **nothing** against this actor and we don't claim it does.

**The central security finding:** the no-escalation clamp `requested ∩ child_floor(creator)` is **vacuous when the creator is unbounded** (the Secretary, the usual depth-0 root, has `bounds=None`=all tools → the intersection returns `requested` verbatim). So the bounds clamp is **drift telemetry, not the gate.** **Consent is the actual security boundary** — and the design makes consent load-bearing (§4).

---

## 2. Decomposition — Aa then Ab (Winston)

| | Sub-story | Deliverable |
|---|---|---|
| **Aa** | **Persisted-owl safety infra** | `origin`/`created_by`/`creation_ceiling` fields on `OwlAgentManifest` + yaml serialization + an `AgentOwlHydrator` that re-clamps `origin=agent` owls at boot (`bounds ∩ ceiling`; fail-closed if the ceiling is missing). **Ships with NO ability to create agent owls** — independently proves a persisted agent-owl is safe even if Ab never shipped. |
| **Ab** | **The `owl_build` tool** | `OwlBuildSpec` + `create/edit/retire` dispatch through one `_authorize_and_clamp`; conservative-default-ceiling; consent (fail-closed off-TTY, toolset shown); existence-check-redirect + name-quality + soft-cap + preset-forced personas; depth-0-only; no-edit-your-betters; persist+audit+snapshot+register-with-rollback (mirror `tool_build`). |

Aa answers "can a persisted agent-owl exist safely?"; Ab answers "can the agent mint one?" — and Aa is genuinely valuable alone (a hand-imported `origin=agent` yaml is already clamped safe).

---

## 3. Story Aa — persisted-owl safety infrastructure

### 3.1 New `OwlAgentManifest` fields (frozen, additive)
- `origin: Literal["human", "agent", "builtin"] = "human"` — provenance discriminator. **Default `"human"`** keeps legacy persisted owls trusted+unclamped+not-agent-editable (the security gates key on `=="agent"`, which the default is not). `register_builtin_personas` explicitly stamps `"builtin"`; `owls_command` (the human path) stamps `"human"`; the agent tool **forces** `"agent"` (§4.2).
- `created_by: str | None = None` — the owl name that minted this owl (provenance for no-edit-your-betters; `None` for human/builtin/legacy).
- `creation_ceiling: BoundsSpec | None = None` — for `origin=agent` owls, the **creator's effective bounds at mint** (`= child_floor(creator)`), snapshotted so boot can re-clamp without the parent chain live. `None` for human/builtin. **Distinct from Epic-2's per-task `TraceContext.creation_ceiling()`** (that ratchet is an *input* to deriving this per-owl snapshot; after mint they decouple — documented, never aliased).

### 3.2 yaml serialization
Extend `commands/owls_helpers.manifest_to_yaml_entry`: emit `origin`, `created_by` (when set), and `creation_ceiling` via `model_dump(mode="json")` **only when not None** (a `None` ceiling must NOT serialize as `{}` — an empty `BoundsSpec` means "deny all", a real clamp, not "no ceiling"). The loader (`Settings.owls` → pydantic) round-trips them; assert frozenset/tuple bounds equality in tests.

### 3.3 `AgentOwlHydrator` (boot re-clamp)
A new `owls/owl_revalidator.py::revalidate_agent_owls(registry)`, run at boot **after `from_settings` + `register_builtin_personas`, before serve** (mirror the DNA-hydrator slot). Per owl, **fail-safe**:
- `origin != "agent"` → skip (legacy/human/builtin untouched).
- `origin == "agent"` and `creation_ceiling is None` → **tamper/corruption signal** (the tool ALWAYS persists a ceiling for agent owls) → **fail-closed**: clamp to an empty/deny-all `BoundsSpec` (the owl loads but powerless) + log loudly.
- `origin == "agent"` with a ceiling → `clamped = effective_bounds(bounds, creation_ceiling)`; if `clamped != bounds` log a clamp event (drift telemetry) and `registry.replace` with the clamped manifest. **Clamp, don't reject** (rejection = silent feature-disable).
- Any exception on one owl → fail-closed for that owl (empty bounds), never abort the loop or crash boot. Idempotent (intersect is idempotent).

This is **consistency belt-and-suspenders** (partial-write / hot-reload / a bounded creator) — explicitly NOT an anti-tamper control (§1).

---

## 4. Story Ab — the `owl_build` tool

Mirrors `tools/meta/tool_build.py::ToolBuildTool` 1:1: pydantic-validate → structured-validate → collision-check → hard checks → **consent (fail-closed off-TTY)** → persist+audit+snapshot → register-live-with-rollback. `ToolManifest`: `action_severity="consequential"`, isolated `toolset_group="meta_write"` (a read-only owl never gets it hydrated), its own `consent_category`. **Added to `_CHILD_EXCLUDED_TOOLS`** so only the depth-0 root owl can mint (sub-agents can't fork-bomb owls); re-checked in `execute` (defense-in-depth).

### 4.1 `OwlBuildSpec` (the agent-facing envelope — NO authority fields)
Frozen pydantic, `extra="forbid"`: `action: Literal["create","edit","retire"]`, `name: str`, `preset: str | None`, `explicit_tools: list[str] | None`, `specialty: str | None`, `model_tier: str | None`. **There is no `origin`, `created_by`, `creation_ceiling`, or `bounds` field** — the agent literally cannot supply authority. `model_validator`: preset XOR explicit_tools; `specialty` required for create. The inner shape is reused via `OwlSpec` → `SpecialistOwlBuilder.build` (the one pure constructor); the tool welds authority on after.

### 4.2 `_authorize_and_clamp` (the one shared authority path — all 3 actions funnel through)
1. `creator = TraceContext.get()["owl_name"]`; `creator_eff = child_floor(creator, TraceContext.creation_ceiling(), registry)`.
2. **Conservative default ceiling (Murat P0):** if `creator_eff is None` (unbounded creator), substitute `SAFE_DEFAULT_CEILING` — a read-only-ish `BoundsSpec` (research/read tools + `ROUTER_TOOLS`; **no shell/exec/write/network**). So the clamp always subtracts; granting a consequential tool requires the human to **explicitly widen at consent** (§4.4).
3. `requested = SpecialistOwlBuilder.build(spec).bounds`; `clamped = effective_bounds(requested, ceiling)`; `dropped = requested.tools − clamped.tools` (reported to the agent).
4. The persisted `creation_ceiling = creator_eff` (or the safe default) — **the floor, snapshotted**.
5. `manifest = built.model_copy(update={"bounds": clamped, "origin": "agent", "created_by": creator, "creation_ceiling": ceiling})` — authority forced server-side, never from the spec.

### 4.3 Behavior guardrails IN THE TOOL (Dr. Quinn — never trust the weak model's judgment)
- **Mandatory existence-check:** before create, similarity-match (name + specialty embedding) against existing owls; a near-match → **refuse + return the existing owl + "delegate to it instead"** (converts mint→delegate). Reuse the embedding registry; fail-open (no embedder → name-equality check only).
- **Name quality:** reject low-information / near-duplicate names (`researcher2`, `helper`, generic nouns colliding with an existing name) → return guidance.
- **Soft cap:** past `MAX_AGENT_OWLS` (default ~5) the tool requires the spec to justify against the existing roster and the consent prompt surfaces "you already have N owls." Hard cap before the consent dialog (a confused-deputy loop can't even spam approvals).
- **Preset-forced personas:** the persona is rendered by the proven `generate_persona` from the preset/role — **never** weak-model freehand `system_prompt` prose. (Explicit `system_prompt` from the agent is rejected/ignored in v1.)
- **Tool description (the framing lever):** leads with *"owl_build is RARE — almost every request is answer-directly or delegate-to-an-existing-specialist. Minting an owl is the exception, for a standing, recurring, named role the human will reuse."* + a **decision ladder** (answer → delegate → only-then mint) + *"Doing a research task once is NOT a reason to mint a research owl — do the task."*

### 4.4 Consent — the real security boundary (Murat P0s)
- **Consequential + consent-gated; FAIL-CLOSED off-TTY** (reuse `tool_build`'s `_consent_or_refuse`): no interactive human (cron/heartbeat/autonomous/durable/sub-agent) → hard refuse, never queue/auto-approve. *The single most important behavior.*
- **The consent prompt renders the resolved facts** (the human is the clamp): owl **name + role**, **the full resolved toolset after clamp** with consequential tools (shell/exec/write/network) **flagged**, the **dropped** tools, the model's **"why" labeled as the model's claim**, the **existing roster** ("you already have: …"), **default-deny** framing.
- **Re-consent on any bounds-widening edit** (adding a tool); a bounds-narrowing edit may skip consent (monotone-safe).

### 4.5 create / edit / retire
- **create:** `_authorize_and_clamp` → collision-check (refuse if `name` exists, `== secretary`, or a built-in persona name) → existence-redirect (§4.3) → consent → persist (`_upsert_to_yaml` + `manifest_to_yaml_entry`, atomic) + audit/snapshot → `registry.register(manifest, source_name="agent_owls")` with rollback (restore yaml snapshot + deregister on any later failure).
- **edit:** load `registry.get(name)`; **no-edit-your-betters** — refuse if `name == secretary`, `origin != "agent"`, or `created_by != creator`. Re-derive the ceiling from the **current** creator's floor and **re-clamp against the original persisted `creation_ceiling`** (monotone ratchet — edit can't launder escalation past the mint clamp). Re-consent if widening. Persist via `replace` + `_upsert_to_yaml`.
- **retire:** same betters-guard → `registry.deregister(name)` (already secretary-guarded — defense-in-depth) + remove from yaml. No rename (name = identity; rename = retire+create).

---

## 5. Security model (the spine — non-negotiable)
- **Consent is the gate** (the clamp is no-op for unbounded creators): fail-closed off-TTY + toolset shown + re-consent on widening (§4.4).
- **Conservative default ceiling** so even an unbounded creator's owls start safe; consequential tools require explicit human widening (§4.2).
- **Authority forced server-side** — `OwlBuildSpec` has no authority fields; `origin`/`created_by`/`creation_ceiling` are computed (§4.1/4.2).
- **no-edit-your-betters** — `origin==agent AND created_by==caller`, runtime-checked in the tool + registry secretary-guard (§4.5).
- **depth-0 only** — `_CHILD_EXCLUDED_TOOLS` (§4).
- **boot re-clamp** — Aa's revalidator (consistency, not anti-tamper; §3.3).
- **Delegation floor still holds** — a *narrow* owl delegating to a new broad owl is still clamped to the narrow delegator's `child_floor` (Epic-2 S2). Test it (§7) — it's load-bearing.

---

## 6. Data flow

```
AGENT TURN (depth-0 root only; owl_build excluded at depth>0):
  owl_build(OwlBuildSpec)  [consequential]
    _authorize_and_clamp:
       creator_eff = child_floor(caller, TraceContext.creation_ceiling(), registry)
       ceiling = creator_eff or SAFE_DEFAULT_CEILING          # unbounded → safe default
       requested = SpecialistOwlBuilder.build(spec).bounds
       clamped, dropped = requested ∩ ceiling, requested−clamped
    existence-check → near-match? refuse + suggest delegate
    name-quality + soft-cap gates
    manifest = built.model_copy(origin=agent, created_by=caller, creation_ceiling=ceiling, bounds=clamped)
    CONSENT (fail-closed off-TTY; prompt shows name+role+resolved-toolset[flagged]+dropped+roster)
       → denied/off-TTY → refuse (no write, no register)
    snapshot yaml → _upsert_to_yaml(manifest_to_yaml_entry) → audit → registry.register(rollback on fail)

BOOT (Aa):
  from_settings → register_builtin_personas → revalidate_agent_owls(registry):
     per origin=agent owl: ceiling? clamp bounds∩ceiling : FAIL-CLOSED empty bounds
  → serve  (assemble reads registry.get(owl) — sees the clamped agent owl)
```

---

## 7. Testing (TDD; mock only the AI provider + consent)
**Aa units** — manifest `origin`/`created_by`/`creation_ceiling` defaults + frozen; `manifest_to_yaml_entry` serializes them (None ceiling → key absent, NOT `{}`); yaml round-trip (bounds frozenset equality); `revalidate_agent_owls`: re-clamps an `origin=agent` owl whose persisted bounds ⊃ ceiling; **fail-closed** on `origin=agent` + None ceiling (→ empty bounds); skips human/builtin; per-owl fail-safe (one bad owl doesn't abort); idempotent.

**Ab units** — clamp: requested ⊃ creator_eff → clamped + `dropped` reported; **unbounded creator → SAFE_DEFAULT_CEILING applied** (shell requested → dropped unless consent-widened); `OwlBuildSpec` has **no origin/bounds field** (constructing with one raises); `origin` forced `"agent"` server-side; no-edit-your-betters (edit/retire secretary/human/builtin/other-agent's owl → refused); collision-check (create existing/secretary/builtin → refused); existence-redirect (near-match → refuse+suggest); name-quality reject; soft-cap; persona is preset-derived (freehand rejected); **consent fail-closed off-TTY** (no TTY → refuse, no write); consent denied → no yaml write + no register (snapshot untouched); register failure → yaml rolled back.

**Gateway journeys (`tests/journeys/` or `tests/tools/meta/`, mirror `test_tool_build_gateway.py`)** —
- **J1** root owl mints a researcher (consent granted) → persisted + registered + survives a simulated restart (fresh `from_settings` + revalidate) with **clamped** bounds.
- **J2 (load-bearing security)** the agent requests a `coder` with `shell` under an **unbounded** creator → without consent-widening, `shell` is **dropped** (conservative default) → the persisted owl has no shell; **off-TTY** variant → mint **refused** entirely.
- **J3** the agent tries to retire the **secretary** / edit a **human** owl → refused.
- **J4** a **depth>0 sub-agent** cannot call `owl_build` (excluded + execute-refused).
- **J5 (delegation floor)** a **narrow** owl delegating to a newly-minted broad `coder` is still clamped to the narrow delegator's floor (can't reach the coder's shell).

---

## 8. Out of scope / deferred (tracked)
| Item | Why | Where |
|---|---|---|
| Freehand agent-authored `system_prompt` | weak-model persona quality/safety; preset-derived only in v1 | follow-up (validated/gated) |
| Owl rename via edit | name = identity (rename = retire+create) | — |
| Anti-FS-tamper integrity (signatures on yaml owls) | offline tamperer is out of scope (owns the box) | never (by threat model) |
| Bulk/batch create | YAGNI | — |
| Per-axis (fs/network/data) clamp reporting | only tools axis enforced today (Epic 3 grows it) | Epic 3 |
| Capability-scaled `SAFE_DEFAULT_CEILING` / soft-cap | constants for now | refinement |
