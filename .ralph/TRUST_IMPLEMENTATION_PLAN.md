# Trust & Capability — Implementation Plan

Spec: [[TRUST_ARCHITECTURE.md]]. Build the FULL fix, honesty-first, no patches.
Method: subagent-driven, story-granular commits, QA+dev review, gateway/eval test per story, reuse-first. Ships ON.

## STATUS: TS1+TS2 @9d421e82 ✅ · TS3 @140739a7 ✅ · TS4 @743c8d91 ✅ · TS5+TS6 @90a64bce ✅ · TS7+TS8 @0572643c ✅ · TS10 @32e6e1da ✅ — next: TS9+TS11 then TS12, TS13

## Epic T1 — Stop the lie (ADR-T2 + ADR-T1)
- **TS1. effect_class on tools**: add `effect_class` to ToolManifest (`creates_persistent_entity|sends_message|schedules|None`). Tag owl_build, skill_manage, cronjob, send_file/telegram send, etc. Read-only tools = None.
- **TS2. Creation self-verification**: owl_build (+skill_manage) set `ToolResult.verified=True` ONLY after world-reads (yaml exists+parses, in reachable registry, job row exists if scheduled). ok=True + failed read → verified=False. Reuse the verification primitive (`tools/verification.py`, `ToolResult.verified`).
- **TS3. Ledger-driven overclaim veto**: overclaim_gate consumes the per-turn DecisionLedger/ToolResult truth (NOT prose). A success claim whose effect_class has no `verified==True` producer → mechanical honest-floor rewrite. Default-deny unmapped success verbs over any non-green tool. `unknown`→floor. Meta-test: every effect-classed tool has a claim-class binding or build fails.
- **TS4. Capability manifest + charter split**: runtime capability manifest from a reachability probe (proactive delivery consuming queue? scheduler ticking? web reachable?) injected like instinct constraints; no tool names. Charter principle: forbid invented "can't" (capability denial), require honesty on consequence-gating. Reuse health contributors for the probe.

## Epic T2 — Grounding (ADR-T3)
- **TS5. Retrieval-gated external claims**: an `informational-external` answer requires a web_search/web_fetch ran this turn (ledger check); zero retrieval → floored. Empty result → honest "nothing new", never fabricate.
- **TS6. Citation integrity**: every URL in the answer must be in the fetched-source set; unfetched URLs stripped; if stripping guts it, floor. Soft entity-in-source backstop (non-blocking).

## Epic T3 — Creation routing (ADR-T4)
- **TS7. Disjoint descriptions**: rewrite owl_build vs skill_manage tool descriptions along who(owl)/how(skill). owl = named persistent persona, schedules, messages you; skill = a procedure an owl invokes. Remove the stale "raise NotImplementedError" docstring on owl_build.
- **TS8. Schedule-as-slot**: owl_build accepts a schedule/cadence; "every 2h"/"daily"/"remind me" → lifecycle=scheduled + CronTrigger (reuse UniOwl trigger + resumable slot-filling). Underspecified cadence → ask.

## Epic T4 — Proactive UX + scheduled-owl safety (ADR-T5)
- **TS9. Trustworthy confirmation**: creation reply proves it — next-fire timestamp + an immediate real sourced poke + one-tap off-ramp; first unprompted poke self-references. (Reuse roster/display_name.)
- **TS10. Scheduled-job honesty + safety**: the goal_execution/poke path enforces the grounding floor (empty→"nothing new"); quiet hours (reuse heartbeat quietHours, coalesce); per-owl daily research budget (loud on exhaust); dedup recent pokes (cosine/pellet); single-flight lock; durable Telegram target.
- **TS11. STOP/SNOOZE**: natural-language stop/pause/snooze/"too much"/resume → pause/adjust the owl's schedule (pause≠delete). Reuse vocative routing + owl edit + reconcile.

## Epic T5 — Prove it (eval suite + live)
- **TS12. Acceptance eval suite** (CI gate): the 8 evals in TRUST_ARCHITECTURE.md, all asserting on ledger + world-reads, never prose. Wire as a gate.
- **TS13. Live re-test**: on the running UniOwl server, re-run the EXACT scenario — "create an agent Brain that pokes me every 2h with real AI news" → assert: owl created (reachable), scheduled job exists @2h, a fired tick does real web_search + delivers a sourced poke, empty cycle says "nothing new", STOP pauses it. Capture traceIds. Merge to main.

## "No limitations" contract (Mary) — enforce, don't remove
Capability unlimited (never invent a can't). Consequences gated: consent on acts in the user's name/wallet, cost caps, quiet hours. All kept limits are about OUTPUTS into the world, framed as enablement.

## Verification (every story)
Unit + gateway/eval (mock only the provider; assert ledger/world-reads) + targeted suite (no full pytest — Jetson hangs). Live at TS13. Keep boot green (reachability block + acceptance_authority on).
