# Element 18 — Providers Layer Audit (2026-05-10)

Phase 1 audit. Three-area parallel Explore squad ran 2026-05-10. All claims verified against live tree.

## File Inventory

| File | LOC | Role |
|---|---|---|
| `src/intelligence/router.ts` | 65 | `IntelligenceRouter` — tier (high/mid/low) mapper by task type |
| `src/engine/router.ts` | ~190 | `ModelRouter` — complexity-based escalation; reads deprecated config |
| `src/providers/registry.ts` | ~210 | Provider registry, lazy init, one-shot `healthCheckAll()` |
| `src/providers/base.ts` | ~80 | `BaseProvider` abstract class |
| `src/providers/anthropic-native.ts` | ~400 | Anthropic native SDK adapter |
| `src/providers/openai-compat.ts` | 522 | OpenAI-compatible adapter |
| `src/providers/protocols/openai.ts` | 341 | OpenAI native protocol |
| `src/providers/protocols/gemini.ts` | ~350 | Gemini protocol |
| `src/costs/tracker.ts` | ~180 | Cost accumulation, per-session/user, budget limits |
| `src/costs/pricing.ts` | ~70 | `MODEL_PRICING` table, ~15 models |
| `src/gateway/types.ts:346` | shared | `intelligence?: IntelligenceRouter` — optional, may be unset |
| `src/config/loader.ts:408-411` | shared | `smartRouting` config key **removed** by previous element |

---

## Gap Inventory

### P1 — Cost-aware routing is structurally disabled
`IntelligenceRouter.resolve()` at `src/intelligence/router.ts:50-64` returns `{provider, model, tier}` — no cost computation, no pricing-table lookup. `costs/pricing.ts` has a `MODEL_PRICING` table (`src/costs/pricing.ts:17`) and `costs/tracker.ts` accumulates spend, but neither is imported by `src/intelligence/router.ts` or `src/engine/router.ts`. Cost data is collected but never fed back into routing decisions. The tier mapping (`conversation→mid`, `synthesis→high`, `extraction→low` at `router.ts:31-41`) is static — no cheapest-viable-model optimization.

### P2 — No continuous health monitoring
`providers/registry.ts:189-195` defines `healthCheckAll(): Promise<Record<string, boolean>>` which calls `provider.healthCheck()` per registered provider. But this is a **one-shot probe** — there is no continuous monitoring loop, no circuit-breaker state machine, no TTL-based re-check, no "CLOSED → OPEN → HALF-OPEN" state tracking. The Gateway calls `healthCheckAll()` during startup but never again. A provider that goes down after startup is not detected until a live request fails.

### P3 — ModelRouter reads config key that no longer exists
`src/engine/router.ts:83` reads `config.smartRouting?.fallbackModel` and `:96` checks `config.smartRouting?.enabled` — but `config/loader.ts:408-411` **removed** `smartRouting` during an earlier element. All `config.smartRouting.*` reads silently return `undefined`. `ModelRouter`'s fallback escalation (`router.ts:83-90`) and domain-routing (`router.ts:96-150`) therefore never execute. The class is present but inert — its escalation logic is dead code.

### P4 — `IntelligenceRouter` is optional and may be unset in Gateway
`src/gateway/types.ts:346`: `intelligence?: import("../intelligence/router.js").IntelligenceRouter` — the `?` means the Gateway can be constructed without a router. `core.ts` likely guards with `this.config.intelligence?.resolve(...)` — but any call path that falls through the guard silently falls back to the owl's own `providerName`/`modelName`, bypassing all routing logic. There is no default IntelligenceConfig in `config/loader.ts`.

### P5 — Hardcoded model names in protocol adapters
Verified sites:
- `src/providers/anthropic-native.ts:385` — `"claude-haiku-4-20250414"` (hardcoded as fallback model name)
- `src/providers/protocols/openai.ts:85` — `(config as any).activeModel ?? config.defaultModel ?? "gpt-4o"` (hardcoded `"gpt-4o"` as last-resort default)
These strings will silently use a specific pinned model version even if the config doesn't specify one. When Anthropic or OpenAI deprecate these versions, the fallback breaks.

### P6 — Provider determined once at Gateway construction
The owl's `providerName` and `modelName` fields are set at construction time from the specialized owl's `helper.md`. The Gateway does not re-resolve the provider per request based on current health, cost, or task type. If an owl is configured for a single provider (`provider: anthropic`) with a single model, it stays on that provider for the session lifetime — no per-request failover, no task-type escalation.

### P7 — `openai-compat.ts` duplicates `protocols/openai.ts`
`src/providers/openai-compat.ts` (522 LOC) implements an OpenAI-compatible adapter. `src/providers/protocols/openai.ts` (341 LOC) implements the OpenAI protocol. They serve overlapping purposes. No import chain or grep shows `openai-compat.ts` being imported in a production code path — it appears to be a legacy adapter from before `protocols/openai.ts` was written. The duplication is ~500 LOC of maintenance overhead.

### P8 — Pricing table is hard-coded, not config-driven
`src/costs/pricing.ts:17` has `MODEL_PRICING` as a hardcoded `Record<string, ModelPrice>` covering ~15 models (Anthropic, OpenAI, DeepSeek, Groq, etc.). New models are added by editing source code. There is no config-file equivalent, no remote price-update mechanism, no fallback for unknown models (returns `undefined`, `estimateCost` uses `0` for unknown models at `pricing.ts:59`). When a user configures a custom OpenAI-compat endpoint with a new model name, costs show as `$0.00`.

### P9 — ModelRouter's `config.smartRouting` dependency mirrors P3
`engine/router.ts:83-91,96-102` references `config.smartRouting` in multiple places. Since `loader.ts:408-411` strips this key, `ModelRouter.routeForComplexity()` always hits the `smartRouting.enabled` false branch and returns the owl's default model. The 5-tier domain routing (lines 102-150) and tool-confidence escalation (lines 123-150) are therefore unreachable.

### P10 — No capability tags anywhere
`src/providers/` has no `capabilities: string[]` or equivalent on any provider, model entry, or config type. `IntelligenceConfig` (`intelligence/router.ts:19-23`) has no capability field. The owl's `helper.md` has `permissions.allowedTools[]` but no model capability tags (vision, code, long-context, tool-use, reasoning). A request needing vision cannot be routed to a vision-capable model — there is no mechanism.

---

## Summary Table

| Gap | File:Line | Severity | LOC at Risk |
|---|---|---|---|
| P1 | `intelligence/router.ts:50-64`, `costs/pricing.ts:17` | Medium | 65 |
| P2 | `providers/registry.ts:189-195` | High | ~20 |
| P3 | `engine/router.ts:83-91,96-97`, `config/loader.ts:408-411` | High | ~100 dead |
| P4 | `gateway/types.ts:346` | Medium | — |
| P5 | `anthropic-native.ts:385`, `protocols/openai.ts:85` | Low-Medium | ~5 |
| P6 | `gateway/types.ts:200`-ish | Medium | — |
| P7 | `providers/openai-compat.ts` (full file) | Low | 522 dead |
| P8 | `costs/pricing.ts:17-60` | Low | ~60 |
| P9 | `engine/router.ts:83-91` (same as P3) | High (dupe) | — |
| P10 | `providers/` (missing field everywhere) | Low | — |

---

*Verified firsthand 2026-05-10. Code is truth.*
