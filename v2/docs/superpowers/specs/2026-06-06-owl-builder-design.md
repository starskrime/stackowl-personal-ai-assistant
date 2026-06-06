# Owl-Builder — Craft Specialist Owls (Owl Capability Arc, Story 1)

> Let a user *or the owl itself* create/customize a **specialist owl** — persona + model +
> a curated tool set expressed as the Epic-2 `BoundsSpec` + `capability_profile` (which surfaces
> skill-registered tools) — via both an agent tool and the `/owls` command, persisted to
> `stackowl.yaml`. This makes the authz bounds we built in Epic 2 actually *usable* per owl.

**Status:** Design approved (forks resolved, 2026-06-06); pending party-mode hardening
**Builds on:** Epic 2 `BoundsSpec` (per-owl bounds, tools-axis enforced) + the existing `OwlAgentManifest`/`OwlRegistry`/`stackowl.yaml` foundation + the S3 `ToolProposer` (LLM tool suggestion)
**Followed by:** Story 2 (per-owl skill *instruction* injection runtime — the missing subsystem); then delegation self-healing; memory/persona robustness

---

## 1. Problem & what already exists (the check)

Epic 2 gave every owl a `BoundsSpec`, but there's **no UX to set it per owl** — bounds only work if hand-edited into `stackowl.yaml`. `/owls add` creates a bare persona (name/role/tier/provider/temperature/tools) and **cannot** set bounds, capability_profile, or system_prompt; there's **no edit**, and **no agent tool** to create owls. So owls can't be specialized in practice.

**Crucially, the foundation is already complete — this story is mostly EXPOSURE, not new infrastructure:**
- `OwlAgentManifest` (`owls/manifest.py`, frozen) already has `bounds`, `capability_profile`, `tools`, model/tier/provider — every field a specialist needs.
- `OwlRegistry` (`owls/registry.py`): `register`/`get`/`list`/`deregister` (no `update` — edit = `model_copy` + re-register).
- `stackowl.yaml` already **loads** `bounds`/`capability_profile` per owl (`Settings.owls` → `OwlAgentManifest(**entry)`); the gap is the command's serializer (`owls_helpers.manifest_to_yaml_entry`) omits them.
- `capability_profile` gating (presentation, E1-S4 in `execute.py`) and `bounds` enforcement (dispatch, E2-S1) are shipped — a built owl's tool surface + authz work the moment the manifest carries them.
- `ToolProposer` (`pipeline/planner/proposer.py`) already does LLM→validated-tool-name derivation (reusable for "suggest").

**Genuinely missing (what this story builds):** a `SpecialistOwlBuilder` core, hybrid tool derivation (presets + suggest + explicit), the `owl_build` agent tool, `/owls add` extension + `/owls edit` + `/owls clone`, yaml serialization of bounds/capability_profile/skills, and a `skills` record field on the manifest.

---

## 2. Approved decisions

| Fork | Decision |
|---|---|
| Tool/bounds derivation | **Hybrid**: presets + LLM-suggest (reuse `ToolProposer`) + explicit override |
| Surface | **Both**: `owl_build` agent tool + `/owls` command (`add`/`edit`/`clone`), sharing the builder core |
| Skills | Builder records **owned skills** (`skills` manifest field) + surfaces their **tools** via `capability_profile`. The skill-**instruction-injection runtime is Story 2** (a missing subsystem — out of scope here). |

> ⚑ **For party-mode:** (a) **self-extension escalation** — an `owl_build` *tool* lets an owl mint new owls; can a weak/narrow/injected owl create a *more powerful* owl (privilege escalation)? Proposed guard: a tool-created owl's bounds are clamped to ⊆ the creating owl's effective bounds (FR35-style). (b) Scope — both surfaces + presets + suggest + edit + clone in one story; trim? (c) safe-by-construction — presets/suggest must bias least-privilege.

---

## 3. Architecture

### 3.1 `SpecialistOwlBuilder` — the core (`src/stackowl/owls/builder.py`)

`build(request) -> OwlAgentManifest`. Request: `name`, `role`, `description` (free-text intent, feeds system_prompt + suggest), `model_tier`/`provider`, a **tool-spec** (preset name | "suggest" | explicit list | combination), `capability_profile` groups (optional), `skills` (owned names, optional), `temperature`. Steps:
1. **Derive the tool allowlist** (hybrid, §3.2) → a `frozenset[str]` validated against the live `ToolRegistry` catalog (unknown names dropped, never fuzzy-matched — reuse the `ToolProposer` validation discipline).
2. **Build the `BoundsSpec`**: `BoundsSpec(tools=frozenset(derived ∪ MANDATORY_DISCOVERY), caps=<preset caps or None>)`. (Other axes left None — modeled, enforced in Epic 3.)
3. **`capability_profile`**: the toolset-groups covering the derived tools + any skill-owned groups (so skill-registered tools surface in presentation).
4. **`skills`**: record the owned skill names on the manifest (`skills` field, §3.4). Their tools surface via #3; their *instructions* are Story 2.
5. **`system_prompt`**: explicit, else generated from role+description (a clear, language-neutral specialist prompt).
6. **Validate + construct** the frozen `OwlAgentManifest`; the registry's `register` enforces name rules + duplicate guard.

The builder is pure construction (no I/O) — registration + persistence are the callers' (tool/command) job, so it's unit-testable in isolation.

### 3.2 Hybrid tool derivation (`src/stackowl/owls/tool_presets.py` + reuse `ToolProposer`)

- **Presets**: a small dict of role→curated allowlist + safe caps, e.g.:
  - `researcher` → read_file, note_search, web_search, web_fetch, browser_* (read), summarize — **no** write/shell/delete.
  - `coder` → read_file, write_file, edit, apply_patch, search_files, execute_code, shell, process.
  - `writer` → read_file, write_file, web_fetch, summarize, translate.
  - `analyst` → read_file, search_files, web_search, web_fetch, summarize.
  Presets are **safe-by-construction** (least-privilege; destructive tools only where the role needs them).
- **Suggest**: `ToolProposer.propose(description, catalog)` → an LLM-derived, exact-validated allowlist (fail-open to empty → fall back to a minimal read-only preset, never unbounded).
- **Explicit**: a caller-supplied list, validated against the catalog.
- **Combine**: any subset may be merged; result ∪ `MANDATORY_DISCOVERY` (`tool_search`/`tool_describe`) so the owl can always discover more.

### 3.3 Persistence (extend the existing yaml path)

`owls_helpers.manifest_to_yaml_entry` currently serializes name/role/system_prompt/tier/temperature/(provider)/(tools). Extend it to **conditionally serialize** `bounds` (via `BoundsSpec.model_dump`), `capability_profile`, and `skills` when present. The loader (`Settings._YamlSource` → `OwlAgentManifest(**entry)`) already parses them, so this closes the round-trip. No new persistence machinery.

### 3.4 `skills` manifest field

Add `skills: list[str] = []` to `OwlAgentManifest` (additive, frozen-compatible, default empty → every existing owl unchanged). Records the owl's owned skills. In this story it's a **record + a presentation hint** (the skills' toolset-groups feed `capability_profile`); the runtime instruction-injection is Story 2.

### 3.5 Surfaces (shared builder)

- **`owl_build` tool** (`src/stackowl/tools/agents/owl_build.py`) — `action: create | edit | clone`, with the build-request fields. **Consequential** (creates/changes a persistent persona) → consent-gated via the existing `ConsequentialActionGate`. Calls the builder → `registry.register` (or model_copy+re-register for edit) → yaml persist. Returns a structured result (the built owl's name + derived toolset + a note). This is the self-extension surface — see the escalation guard (§5).
- **`/owls` command** (`commands/owls_command.py` + `owls_helpers.py`) — extend `add` with `--preset <p>` / `--suggest` / `--tools <csv>` / `--skills <csv>` / `--capability-profile <csv>` / `--system-prompt <text>`; add `edit <name> [flags]` (model_copy + re-register + re-persist; Secretary-guarded) and `clone <src> <new> [overrides]`. All route through the same `SpecialistOwlBuilder` + persistence.

### 3.6 Edit / clone

- **Edit**: `registry.get(name)` → `manifest.model_copy(update={changed})` → `deregister`+`register` → re-persist. Guard the mandatory Secretary (no deregister/overwrite). Name is the immutable key.
- **Clone**: `get(src)` → `model_copy(update={"name": new, **overrides})` → register + persist. A fast way to template a new specialist from an existing one.

---

## 4. Data flow

```
owl_build tool / /owls add|edit|clone
  request{name, role, description, tier, tool-spec, skills, ...}
    → SpecialistOwlBuilder.build():
        tools = derive(preset | ToolProposer.suggest(description,catalog) | explicit) ∪ discovery
        bounds = BoundsSpec(tools=…, caps=preset_caps)
        [escalation guard §5: bounds ∩= creator_effective_bounds  when created via the tool]
        manifest = OwlAgentManifest(name, role, system_prompt, tier, tools, capability_profile, skills, bounds)
    → registry.register(manifest)  (edit: model_copy+deregister+register)
    → manifest_to_yaml_entry(manifest) → _append/_replace in stackowl.yaml
  later: a turn routed to the new owl → execute presents capability_profile tools + ENFORCES bounds (Epic 2)
```

---

## 5. Error handling / invariants

| Concern | Resolution |
|---|---|
| Unknown tool names (explicit/suggest) | validated against the live catalog; dropped (never fuzzy-matched) |
| Suggest fails / no provider | fall back to a minimal read-only preset (never unbounded, never crash) |
| **Self-extension escalation** (owl mints a more-powerful owl) | when created via the `owl_build` TOOL, the new owl's `bounds.tools` are intersected with the **creating owl's effective bounds** (a narrow/injected owl can't mint a broad one). `/owls` command (human) is not clamped. (Party-mode to pressure-test the exact rule.) |
| Creating/editing owls is consequential | `owl_build` tool is consent-gated; writes to `stackowl.yaml` are the existing audited path |
| Secretary protection | edit/clone/overwrite of the mandatory Secretary is refused (mirrors `deregister`'s guard) |
| Duplicate name on create | refused (registry already raises); `edit` is the update path |
| Manifest is frozen | edits via `model_copy(update=...)` (no mutation) |
| Persistence round-trip | bounds/capability_profile/skills serialize + reload identically (tested) |
| Built owl actually works | a turn routed to the new owl presents its tools + enforces its bounds (Epic 2 dispatch) — proven by a gateway journey |
| Safe-by-construction | presets least-privilege; no destructive tool (shell/delete) unless the role requires it |

---

## 6. Testing (TDD; only the AI provider mocked)

**Builder units (`tests/owls/test_builder.py`)** — preset derivation (researcher excludes shell/write); suggest path (mock `ToolProposer` → validated set; fail → read-only fallback); explicit (unknown dropped); combine ∪ discovery; system_prompt generation; produces a valid frozen manifest with the right `BoundsSpec`/`capability_profile`/`skills`.

**Manifest + persistence (`tests/owls/`, `tests/config/`)** — `skills` field defaults empty (existing owls unchanged); `manifest_to_yaml_entry` serializes bounds/capability_profile/skills; full yaml round-trip (write → reload → identical manifest incl. bounds).

**Registry edit/clone** — edit via model_copy+re-register preserves other fields; clone copies + overrides; Secretary guard refuses edit/overwrite.

**`owl_build` tool** — create/edit/clone happy paths; consent-gated (consequential); unknown-tool validation; **escalation guard**: a creator owl with bounds `{read_file}` cannot mint an owl with `{shell}` via the tool (result clamped to `{read_file}`).

**`/owls` command** — `add --preset researcher` / `--suggest` / `--tools` / `--skills` builds + persists; `edit` updates + re-persists; `clone` templates; round-trips through yaml.

**Gateway journey (`tests/journeys/`)** — build a specialist (e.g., researcher with bounds `{web_fetch, note_search}`) via the tool; route a turn to it; assert it can use `web_fetch` and is **bounds-blocked** from `shell` (the built owl's authz is live end-to-end). A second: clone an owl, edit its tools, confirm the change takes effect on a turn.

---

## 7. Out of scope / deferred (tracked)

| Item | Why | Where |
|---|---|---|
| Per-owl skill **instruction injection** runtime (skills shape behavior per turn) | missing subsystem, not a small add (the check found it) | **Story 2** |
| Skill marketplace / remote install | separate concern | later (plugin/marketplace) |
| Owl-builder GUI/wizard | CLI + tool first | later, if needed |
| fs/network/data/caps bounds *enforcement* on built owls | Epic 3 (bounds modeled, tools-axis enforced) | Epic 3 |
| Interactive bounds questionnaire | preset/suggest/explicit cover it | later, if needed |
