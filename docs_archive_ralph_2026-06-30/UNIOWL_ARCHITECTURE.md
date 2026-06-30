# UniOwl — Agent Creation Platform Architecture

Status: PROPOSED (post BMAD-party, 2026-06-27). Author: Winston + party (Sally/UX, John/PM, Mary/Analyst).
Goal: non-technical users (accountant, librarian, driver) create AI agents ("owls") by chat OR `/agent`/`/owl`, name them ("Tony"), pick a lifecycle, and the platform persists, boots, and routes to them.

## Guiding principle
~80% is already built. Reuse `ClarifyTool`/`ClarifyGateway`, `SpecialistOwlBuilder`/`build_agent_manifest`, the scheduler, `OwlRegistry` YAML persistence, `GatewayScanner`+`FuzzyMatcher`. New = 3 manifest concepts + 1 orchestrator + 1 handler + 1 router rule. No new packages.

## Party convergence (all four agents agreed)
1. **Reuse the clarify primitive** — wire `ClarifyTool` into creation; do NOT build a new wizard engine.
2. **Manifest = single source of truth; scheduler rows = reconciled projection** (Winston's spine). Never imperatively create a cron row on save — it drifts → zombie deliveries (the 24× dropped-delivery class). Reconcile on boot + on every mutation.
3. **`display_name` (human) is separate from `name` (routing slug, `^\w+$`)**. NEVER key operations/jobs on display_name — only on the stable id/name.
4. **Elicitation ≤ 2–4 questions, infer type from the goal, never reject-with-error** (Sally). Buttons for small choice spaces, free text for open ones.
5. **MVP = on-demand named expert**, created conversationally + Hey-Tony routing + registry view. **Defer always-on/scheduled + threshold monitor to Phase 2** (John).
6. **Retire must be transactional** — tear down owned jobs in the same op (Mary). `reachable≠retired` is the nightmare.

## The four ADRs

### ADR-A — Resumable, validator-gated creation (the elicitation flow)
- `owl_build` accepts a **partial manifest dict**. The manifest validator is the state machine: it returns either a complete owl or `MissingFields(fields=[...], partial=...)`.
- On missing fields, the tool raises a `ClarifyRequest` through the existing **ClarifyGateway**; the partial manifest rides in the resume envelope (that IS the session state — no new persistence, no new session object).
- User answer → merge into partial → re-validate → loop until schema satisfied → mint via existing `_create`.
- **Trust boundary:** the schema decides *whether* to ask (deterministic — a weak model can't be trusted to remember required fields); the LLM only decides *how to phrase* the question.
- UX: infer `type`/persona/model/tools from the goal sentence; only ask the irreducible unknowns (lifecycle if ambiguous, name). Never expose DNA/tools/model to the user. Save a draft on abandonment; resume gently; drop on timeout. Partial spec is ephemeral until the user confirms a summary.

### ADR-B — Manifest single source of truth; scheduler = projection (the lifecycle model)
New `OwlAgentManifest` fields:
- `display_name: str` — the spoken human name ("Tony"); slugified to `name` silently.
- `lifecycle: Literal["on_demand","scheduled"] = "on_demand"`.
- `trigger: TriggerSpec | None` — discriminated union: `cron` | `watch` | `threshold`.

Reconcile loop (extends `from_settings` boot reload + runs on create/retire/edit):
- For each `lifecycle=scheduled` owl → idempotent-upsert a scheduler row keyed by owl id, tagged owner=<owl> (provenance marker — reconcile only ever touches owl-owned rows, never hand-made cronjobs).
- For each owl-owned row whose owl is gone / now on_demand / retired → delete.
- Jobs reference owl by stable id, re-read spec at next fire (no in-flight mutation). Rename = label-only write. Retire = transactional teardown.
- Boot does NOT backfill missed runs (coalesce to ≤1 catch-up).

### ADR-C — Generic conditional/threshold monitor handler (Phase 2)
- Model on `WebsiteWatchHandler`, swap content-diff for predicate-eval. Three parts: generic numeric source (tool/URL+extractor, NO market SDK, no `btc` in code), predicate `(op, threshold)`, and **edge-trigger + hysteresis** (store `last_state`; fire only on false→true; re-arm after crossing back / cooldown). Level-triggered on a 1-min cadence = notification flood — hysteresis is the whole point.

### ADR-D — Vocative-position display_name routing ("Hey Tony")
- Register `display_name`s into the `FuzzyMatcher` candidate set in `GatewayScanner.scan` before the secretary fallback.
- Match name only in **vocative position** (turn-initial `^<name>[,: ]` or turn-terminal `, <name>\??$`) — NOT name-anywhere (kills "Tony said…" hijack). Do NOT key on "Hey" (hardcoded-English stopword violation) — match the name token positionally, greeting-independent.
- Unicode `\p{L}`, NFC-normalize, case-fold; RTL-aware (vocative position is logical turn-boundary, not leftmost byte).
- Fail-safe: ambiguous / below-threshold / two close candidates → fall through to secretary or ClarifyTool disambiguation, NEVER silently hijack.
- **Group chats:** NL name routing requires the name be registered owl-owned in that channel (or `@`-only) — prevents an owl "Sarah" answering messages meant for a human Sarah.

## Phasing (John)
- **Phase 1 (MVP):** ADR-A + ADR-D (on-demand subset) + `display_name` + registry view (`/agents` roster, surfaces existing plumbing). Proves "non-tech user creates a *useful* agent by chatting." On-demand only.
- **Phase 2:** ADR-B lifecycle + ADR-C monitor + guardrails (cost ceiling per-agent+global, interval floor ≥5min, notification budget, sprawl cap, circuit-break+1-notify on repeated failure).
- **Phase 3:** management at scale — edit/clone/pause/retire UX, sharing, marketplace.

## Top risks (named by the party)
- **Manifest/scheduler drift** (Winston) → mitigated by ADR-B projection contract.
- **Flow silently becoming a form** (Sally) → discipline: 1 sentence + ≤2 taps + name + done; everything else default-and-adjust-later.
- **Disappointing first agents from weak elicitation** (John) → the orchestrator must be a genuinely good interviewer; this is the one place not to be lazy.
- **Orphaned scheduled job from a retired/renamed owl in a multi-party channel** (Mary, most dangerous) → transactional retire + per-channel owl-ownership gate on NL routing.

## Founder decisions still needed (Mary)
1. Identity scope: per-user / per-channel / global? (rec: names unique per-user; NL routing gated per-channel)
2. Always-on min interval + per-user quota (Jetson resource limit).
3. Failure policy: retry count, circuit-break threshold, who's notified, pause vs kill.
4. "Retire" = soft-disable (recoverable) vs hard-delete?
5. Group-chat NL routing: applies, or `@`-only there?
6. Confirm chat-minted owls stay under `SAFE_DEFAULT_CEILING` (no accidental shell/write).

## Success metric (John)
Activation: % of created agents invoked again with a positive/continued interaction within 48h (target >60%/week). Leading indicator: median clarifying-questions-to-first-useful-response.

## Build estimate
Zero new packages. ~3 manifest fields, 1 elicitation orchestrator (wiring clarify↔owl_build), 1 reconcile loop, 1 monitor handler (Phase 2), 1 scanner routing rule. The hard part is the reconciliation contract + the interviewer quality, not code volume.
