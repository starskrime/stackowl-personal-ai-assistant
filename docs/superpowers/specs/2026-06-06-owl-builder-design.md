# Owl-Builder — Craft Specialist Owls (Owl Capability Arc, Story 1)

> A **human** builds/customizes a **specialist owl** via `/owls` — persona + model + a curated
> toolset (the Epic-2 `BoundsSpec`) — that knows its specialty (generated persona = the
> "compass") and **delegates out-of-scope work** instead of dead-ending (the boundary-router).
> The value isn't more capability — it's **drift-reduction on a weak local model** (focus +
> safety). Reshaped from a maximal draft by party-mode (Winston/Murat/Dr. Quinn/Amelia).

**Status:** Design reshaped + approved (2026-06-06); pending spec re-review
**Builds on:** Epic 2 `BoundsSpec` (per-owl bounds, tools-axis enforced) + existing `OwlAgentManifest`/`OwlRegistry`/`stackowl.yaml` + existing delegation (`delegate_task`)
**Followed by:** the **agent `owl_build` tool** (self-extension — needs the delegation-origin authority clamp + session-only model Murat specced); LLM-suggest derivation; clone; per-owl skill *instruction* injection runtime

---

## 1. Problem, value, and what already exists

Epic 2 gave every owl a `BoundsSpec`, but there's **no UX to set it per owl** — bounds only work via hand-edited yaml. `/owls add` makes a bare persona and can't set bounds/capability_profile/system_prompt; there's **no edit**. So owls can't be specialized in practice.

**The value (Dr. Quinn):** a specialist owl is *not* "more capable" — by capability it's strictly dominated by the general Secretary (fewer tools = does less). Its win is **reduced variance → reliability**, which matters most on a weak local model (the box runs gemma, which *causes agentic loops*). A narrow toolset shrinks the action space (fewer wrong turns); a sharp persona shrinks wrong intentions. **But a narrow owl is only additive if it DELEGATES out-of-scope work** — otherwise it dead-ends/loops and is a worse Secretary. So the builder must produce owls that are **focused + self-healing at the boundary**, not just narrowed.

**Foundation already exists (this is mostly exposure):** `OwlAgentManifest` (frozen) already carries `bounds`, `capability_profile`, `tools`, model/tier/provider; `OwlRegistry` (register/get/list/deregister, no update); `stackowl.yaml` already *loads* bounds/capability_profile; `capability_profile` presentation gating + `bounds` dispatch enforcement are shipped; `delegate_task` delegation exists. **Genuinely new:** a builder, role presets, the `skills` field, yaml *serialization* of bounds/capability_profile/skills, an atomic `registry.replace`, and `/owls edit` + extended `add`.

---

## 2. Approved decisions (party-mode reshape)

| Fork | Decision |
|---|---|
| Surface | **Human `/owls` command only** (add + edit). The **agent `owl_build` tool is DEFERRED** — minting owls via a tool is an escalation minefield (proxy-via-the-mandatory-Secretary needs delegation-origin authority propagation; tool-persisted owls are a restart-surviving backdoor). Human = authority → no clamp needed. |
| Boundary-router | **In.** Every specialist auto-includes `delegate_task` + a generated persona line "do your specialty; delegate the rest to the secretary." Self-healing + additive. |
| Derivation | **Presets + explicit only.** LLM-suggest deferred (non-determinism + the clamp-intersection it would need). |
| Verbs | **add + edit.** Clone deferred (a thin `model_copy` follow-up). |
| Structure | **One `OwlSpec` lifecycle** (`derive → validate(once) → persist → instantiate`) with a derivation-strategy slot; **build (pure) split from persist (gated, atomic)**; `registry.replace`; yaml-as-source-of-truth write-through. |

---

## 3. Architecture

### 3.1 `SpecialistOwlBuilder` — the one lifecycle (`src/stackowl/owls/builder.py`)

`build(request) -> OwlAgentManifest` — **pure, no I/O** (persistence is the caller's job; resolves both the future tool-safety seam and edit atomicity). Request: `name`, `role`, `description`, `model_tier`/`provider`, a **`BoundsDerivation`** (preset | explicit — a strategy interface, suggest is a future impl), optional `capability_profile`, optional `skills`, temperature.

Steps (one `validate()`, written once):
1. **Derive tools** via the strategy → a `frozenset[str]`, **validated against the live `ToolRegistry` catalog** (unknown names dropped, never fuzzy-matched).
2. **Boundary-router defaults:** union the derived set with `delegate_task` (so the owl *can* hand off) + `tool_search`/`tool_describe` (discovery). → `BoundsSpec(tools=…, caps=preset_caps)`.
3. **Persona = the compass + the router instruction:** generate (or accept explicit) a `system_prompt`: "You are a `<role>`. You handle `<specialty>` directly with your tools. For anything outside your specialty, hand off to the secretary via `delegate_task` — do not attempt tools you don't have." (Language-neutral, per project rules.)
4. **`capability_profile`** = toolset-groups covering the derived tools (+ skill-owned groups) so the right tools are *presented*.
5. **`skills`** recorded (§3.4).
6. Construct + validate the frozen `OwlAgentManifest` (registry `register` enforces name rules).

### 3.2 Presets + explicit (`src/stackowl/owls/tool_presets.py`)

Small role→curated-allowlist dict, **safe-by-construction** + each auto-carrying `delegate_task`+discovery:
- `researcher` → read_file, note_search, web_search, web_fetch, browser (read), summarize — **no** write/shell/delete.
- `coder` → read_file, write_file, edit, apply_patch, search_files, execute_code, shell, process.
- `writer` → read_file, write_file, web_fetch, summarize, translate.
- `analyst` → read_file, search_files, web_search, web_fetch, summarize.

`explicit` = a caller list, catalog-validated. Both flow through the same `BoundsDerivation` interface → the same `validate()`.

### 3.3 Persistence — yaml as source of truth, atomic, build/persist split

A `persist(manifest)` step (separate from `build`): serialize → write yaml **atomically** (temp file + `os.replace`) **first**, then mirror into the registry via a new **`OwlRegistry.replace(manifest)`** (atomic in-memory swap — no deregister-then-register empty-window). On a register failure, compensate (rewrite prior yaml).
- Extend `owls_helpers.manifest_to_yaml_entry` to serialize `bounds` via **`model_dump(mode="json")`** (frozenset/tuple → list; ruamel can't represent frozenset/tuple), plus `capability_profile` and `skills` (currently omitted). The loader (`Settings._YamlSource` → `OwlAgentManifest(**entry)`) already re-coerces list→frozenset/tuple — closing the round-trip.
- Verify/ensure `_append_to_yaml` uses temp+`os.replace` (a half-written `stackowl.yaml` corrupts *every* owl — fix if it writes in-place).

### 3.4 `skills` manifest field

Add `skills: tuple[str, ...] = ()` to `OwlAgentManifest` (additive; tuple = frozen-safe; default empty → existing owls + legacy yaml unchanged; `extra="forbid"` requires the field exist). Records owned skills; their toolset-groups feed `capability_profile`. The skill-**instruction-injection** runtime is a later story.

### 3.5 `/owls` command (the surface)

- `add <name> --role <r> --tier <t>` extended with `--preset <p>` | `--tools <csv>` (the derivation), `--skills <csv>`, `--capability-profile <csv>`, `--system-prompt <text>`. Routes through `SpecialistOwlBuilder.build` → `persist`.
- `edit <name> [flags]`: `registry.get(name)` → `manifest.model_copy(update={changed incl. bounds/skills})` → `persist` (atomic write-through + `registry.replace`). **Secretary is mandatory + guarded** (its `deregister`/edit is refused). Name is the immutable key (no rename in S1).
- `list` already shows owls; extend its display to show the toolset/preset.

### 3.6 Boundary-router (Dr. Quinn's leverage point) — builder-only, reuses delegation

No seam change. The router is two builder defaults: **(a)** `delegate_task` is always in a specialist's bounds (it *can* hand off), and **(b)** the generated persona *instructs* delegation for out-of-scope work. So a researcher asked to run code delegates to the secretary instead of dead-ending against its bounds. (Enhancing the bounds-block message to suggest delegation is a possible later refinement; not needed in S1.)

---

## 4. Data flow

```
/owls add|edit  (HUMAN — the authority)
  request{name, role, description, tier, derivation=preset|explicit, skills, ...}
    → SpecialistOwlBuilder.build():           # pure
        tools = derive(preset|explicit) ∪ {delegate_task, tool_search, tool_describe}
        bounds = BoundsSpec(tools=…, caps=preset_caps)
        system_prompt = compass + "delegate out-of-scope to the secretary"
        manifest = OwlAgentManifest(name, role, system_prompt, tier, tools,
                                    capability_profile, skills, bounds)
    → persist(manifest):                       # gated, atomic
        yaml: serialize bounds=model_dump(json) → temp file → os.replace   (source of truth)
        registry.replace(manifest)             # atomic in-memory swap
  later: a turn routed to the specialist
    → presents capability_profile tools; ENFORCES bounds (Epic 2)
    → out-of-scope tool blocked → owl delegates via delegate_task (boundary-router)
```

---

## 5. Error handling / invariants

| Concern | Resolution |
|---|---|
| Unknown tool names | catalog-validated, dropped (never fuzzy-matched) |
| frozenset/tuple yaml serialization | `bounds.model_dump(mode="json")`; reload re-coerces; round-trip tested (frozenset equality) |
| `skills` additive | `tuple[str,...]=()`; legacy yaml without it loads; `extra="forbid"` satisfied |
| Edit atomicity (registry+yaml dual-write) | yaml source-of-truth, temp+`os.replace` first, then `registry.replace`; compensate on register failure |
| Half-written yaml corrupts all owls | atomic temp+`os.replace` only (fix `_append_to_yaml` if in-place) |
| No empty-slot window on edit | `registry.replace` (atomic swap), not deregister+register |
| Secretary protection | edit/overwrite of the mandatory Secretary refused (existing guard) |
| Duplicate name on create | refused (registry raises); `edit` is the update path |
| Specialist dead-ends on out-of-scope | boundary-router: `delegate_task` in bounds + persona instructs delegation |
| Built owl actually works | a turn routed to it presents its tools, enforces its bounds, and delegates out-of-scope — proven by a gateway journey |
| Safe-by-construction | presets least-privilege; no shell/delete unless the role needs it |
| **No escalation surface** | human-only `/owls` (the human is the authority) → no minting clamp needed; the agent tool (which would need the clamp) is deferred |

---

## 6. Testing (TDD; only the AI provider mocked)

**Builder units (`tests/owls/test_builder.py`)** — preset derivation (researcher excludes shell, includes `delegate_task`+discovery); explicit (unknown dropped); generated persona contains the "delegate out-of-scope" instruction; produces a valid frozen manifest with the right `BoundsSpec`/`capability_profile`/`skills`; `validate()` exercised once across both strategies.

**Persistence (`tests/owls/`, `tests/config/`)** — `manifest_to_yaml_entry` serializes `bounds` (json mode) + `capability_profile` + `skills`; full yaml round-trip (write→reload→identical manifest, frozenset equality); atomic write uses temp+`os.replace`; `registry.replace` swaps atomically (the owl is never absent mid-edit).

**Manifest field** — `skills` defaults `()`; legacy yaml without `skills` loads; a yaml with `skills` loads.

**Command** — `/owls add --preset researcher` builds+persists a researcher (bounds exclude shell, include delegate_task); `--tools` explicit; `edit` updates a field + re-persists (other fields preserved, bounds/skills carried in the `model_copy`); **edit of Secretary rejected**.

**Gateway journey (`tests/journeys/`)** — human builds a `researcher` specialist via `/owls add --preset researcher`; route a turn to it: it uses `web_fetch` (in bounds), is **bounds-blocked** from `shell` (Epic-2 enforcement live), and **delegates** an out-of-scope coding request via `delegate_task` (the boundary-router — not a dead-end). Reload (parse yaml) → the specialist persists with its bounds intact.

---

## 7. Out of scope / deferred (tracked)

| Item | Why | Where |
|---|---|---|
| Agent `owl_build` **tool** (owl mints owls) | escalation minefield: delegation-origin authority clamp, session-only/ephemeral, no-edit-your-betters, presets-clamped, yaml-revalidate-on-load (Murat's P0 ledger) | dedicated follow-up (self-extension) |
| LLM-**suggest** derivation | non-determinism + needs the clamp it would feed; land alone | S1.5 (a `BoundsDerivation` impl) |
| **clone** | thin `model_copy` convenience | follow-up after edit |
| Per-owl skill **instruction injection** runtime | missing subsystem (cage-without-compass is solved by the system_prompt; instruction-injection is the *next* multiplier) | Story 2 |
| Bounds-block message → "delegate" hint at the seam | router works via persona+delegate_task; seam-message polish optional | later |
| fs/network/data/caps bounds **enforcement** | Epic 3 | Epic 3 |
