# PRD Quality Review — StackOwl De-complication — Unblock Jarvis

Reviewed against `.claude/skills/bmad-prd/assets/prd-validation-checklist.md`. Stakes calibration: internal solo-operator tool, technical de-complication of an existing system, owner decisions pre-locked, consumed by a fresh implementation session. Judged at internal stakes, not launch stakes.

## Overall verdict

This is a strong PRD for its shape: a brownfield capability spec with a real thesis ("remove the damping, not the safety"), locked decisions marked as decisions, testable consequences on every FR, and — verified by spot-check — accurate code references throughout the addendum (clamp values, latch bands, feedback no-op, `cloud_enabled` triplication, backend default, check_in Telegram coupling all confirmed at HEAD a72c40a7). What's at risk is concentrated in two places: FR-9's sticky-routing trigger condition is underspecified in a way that hides the design's hardest sub-problem, and the FR-14/15 soak-then-delete sequence has no concrete exit criterion for an irreversible step. Both are small fixes. **READY-WITH-FIXES.**

## Decision-readiness — strong

Decisions are stated as decisions, with provenance: "[owner decision: asyncio dies]" (§4.4), "[owner-approved narrowing]" (FR-10), "[Owner-approved design change: schedule-time gate → delivery-time gate]" (FR-7), and the addendum records the rejected alternative (asyncio-keeps/LangGraph-dies, reversed by owner, decision-log #1) including why the reversal was cheap. Trade-offs name what was given up: FR-8 explicitly accepts missing verbose reactions; FR-10 names what stays always-on when the judge becomes conditional. Open Items are genuinely open (latch retune values flagged as starting points tunable against SM-3; three defaults taken without explicit owner answer are flagged for kickoff rather than smuggled in). `.decision-log.md` exists in the folder (dotfile).

### Findings
- **low** Consent boundary could bite mid-execution (§4.5 FR-16) — "flags gating real features go to a batched [consent] ask" leaves the implementer to judge per-flag which side of the line it falls on; a wrong call violates the never-disable-features house rule. *Fix:* one sentence: when in doubt whether a flag gates a real feature, treat it as [consent].

## Substance over theater — strong

No furniture. There are no personas beyond the single real operator ("Boss"), no differentiation section, no market sizing. The NFRs are the opposite of boilerplate — each encodes a specific house rule with a specific bound (NFR-1 "byte-identical on the existing honesty test corpus"; NFR-2 "never full pytest on this box (hangs)"). The Vision (§1) could not be swapped into another PRD: it names the exact pathology (~19 hops, 3–4 LLM calls, ±0.1 clamp inertness, Telegram-coupled proactivity) and the exact preservation constraint (floors still catch, vetoes still block). The Glossary earns its place — every term (gate cascade, delivery gate, shared seam, directive latch, UndeliveredOutbox, soak) is used downstream.

No findings.

## Strategic coherence — strong

The thesis is explicit and load-bearing: the system is fully wired but over-hedged; remove damping, keep safety. Every feature traces to it — F1 un-damps learning, F2 un-couples proactivity from one channel, F3 removes redundant hedging from the hot path, F4 kills the duplicated backend that doubled the hedging surface, F5/F6 are honestly labeled background backlog that "never blocks F1–F4." Success metrics validate the thesis rather than activity (SM-1 counts LLM calls per clean turn; SM-3 measures whether evolution ever fires; SM-5 counts gate modules 9 → 4), and both counter-metrics guard exactly the two ways the thesis could fail (CM-1 honesty regression, CM-2 sticky-routing misroutes). Implementation order (§8) follows dependency logic (shared seam before backend flip, soak before delete), not "easy first."

No findings.

## Done-ness clarity — adequate

Every FR has at least one testable consequence, most name the exact log line, suite path, or byte-identity bar, and the addendum gives per-FR verification commands. Two spots fall short of the bar the rest of the document sets:

FR-9 (sticky routing) is the least specified consequence in the PRD and the most dangerous one (CM-2 exists because of it). "A short same-session follow-up with no direct owl address" — "short" is unbounded, and "new-topic messages invoke the LLM router as before" is circular: deciding a message is new-topic without calling the router *is* the design problem, and the PRD hands it to the implementer silently. The addendum repeats the FR without resolving it.

FR-14/FR-15's gate on an irreversible action is soft: "several days of clean logs (no InfrastructureError from graph invocation, no checkpoint errors)" — addendum says "≥ a few days." No N, and "clean" is two named error classes plus vibes. Deleting `asyncio_backend.py` (FR-15) is the one step in this PRD with no cheap undo.

### Findings
- **high** FR-9 bypass condition underspecified and circular (§4.3 FR-9; addendum FR-9) — "short" has no bound and "new-topic" detection without the router is unexplained; a fresh session will invent a heuristic unreviewed, on the exact FR the PRD itself flags as misroute-risky (CM-2). *Fix:* specify the mechanical rule (e.g. char/token ceiling + same-session recency window + no direct address per scanner; anything not matching → LLM route), or mark it `[ASSUMPTION]` with the intended shape so the kickoff review catches it.
- **medium** Soak exit criterion vague before an irreversible delete (§4.4 FR-14/FR-15; addendum FR-13/14/15) — "several days of clean logs" has no N and no query definition. *Fix:* define soak = N calendar days (pick one) with the specific jq queries that must return zero rows.
- **low** FR-8 threshold is approximate in a testable consequence ("≥ ~200 chars") — the tilde makes the consequence untestable as written, though the ceiling is honestly accepted. *Fix:* pick the constant in the PRD (200) and let the addendum keep the tuning note.
- **low** FR-1's "simulated multi-night run" harness is unspecified — the observable (log line `[dna] injector.inject: exit — directives appended`) is concrete, but how to simulate multiple nights is left open. *Fix:* one line in the addendum (e.g. repeated dry-run invocations with synthetic feedback rows).

## Scope honesty — strong

The strongest dimension. §7 is a real Non-Goals section that pre-empts exactly the inferences a fresh session would otherwise make (`_drain_next` "race" is by design — no action; no new subsystems; no standalone monster-file splits). `[consent]` tags mark every action requiring owner sign-off (FR-19, real-feature flags in FR-16), honoring the never-disable rule. The single `[ASSUMPTION]` (latch retune values) is tagged and given a tuning loop (SM-3). The addendum's §A.4 "Non-issues" section — audit findings investigated and deliberately not actioned — is scope honesty most PRDs never attempt. Open-items density is low and appropriate for pre-locked internal stakes.

No findings.

## Downstream usability — strong

Built for its actual consumer (a fresh implementation session): Glossary terms are used identically across FRs, UJs, and SMs; FR-1..22 / UJ-1..3 / SM-1..5 / CM-1..2 are contiguous and unique; every FR↔UJ↔SM cross-reference resolves; the PRD/addendum split is clean (requirements vs. file-line how) with FR numbers as the join key. Brownfield accuracy — the rubric's non-negotiable here — was spot-checked and holds: `_DELTA_LOWER/_UPPER` ±0.1 (evolution.py:49-50), `HIGH_ENTER/EXIT` 0.70/0.60 (directive_latch.py:12-13), tone/length/content no-op with format-only capture (feedback.py:110-116), check_in refusing to seed without a resolvable Telegram owner (assembly.py:~392-403), `cloud_enabled` at settings.py:201/289/341, backend default "asyncio" at :378, `defer_under_load` handler property real.

### Findings
- **medium** Undefined internal jargon at load-bearing points (§4.1 FR-4 "LS7 seam"; addendum "F088", "LS2/LS4", "STEER/STOP/NEW", precedence-ladder entries `applied_lessons`/`recovery`/`command_hint` absent from the Glossary) — the fresh session inherits none of the sessions that coined these codes; each is an extra archaeology trip. *Fix:* one-line Glossary entries for LS7 and F088 (the two that gate correctness: the skill-success seam and the persist-after-floors ordering invariant); the rest are discoverable from code comments.
- **low** No Assumptions Index — a single inline `[ASSUMPTION]` plus the Open Items list makes roundtrip trivially checkable, so this is a formality note only.

## Shape fit — strong

Correctly shaped as a brownfield capability spec for a single-operator internal tool. UJs are deliberately light (three, all with the named protagonist "Boss") and declared as such (§2.3 "Internal tool, single operator — light form"); they earn their place by anchoring SM-2/SM-4 and FR realizations rather than decorating. SMs are operational, matching the rubric's expectation for this shape. The tech-how lives in the addendum by design and the PRD says so (§0) — no shape forcing in either direction. New behavior vs. existing behavior is consistently distinguished (Glossary marks NEW terms; audit Part A describes current state, Part B the change).

No findings.

## Mechanical notes

- **Glossary drift:** none found — "delivery gate", "shared seam", "lessons_index", "UndeliveredOutbox", "soak" used consistently across PRD and addendum.
- **ID continuity:** FR-1..FR-22 contiguous, no gaps or duplicates; UJ-1..3, SM-1..5, CM-1..2 clean; every "Realizes UJ-n" and SM→UJ reference resolves.
- **Cross-refs:** `.decision-log.md` exists (dotfile — an `ls` without `-a` misses it; worth a parenthetical in §0 so the implementing session doesn't conclude it's missing). `addendum.md` exists. Addendum line references verified accurate at a72c40a7 as claimed.
- **Assumptions roundtrip:** the one inline `[ASSUMPTION]` (FR-1 retune values) appears in Open Items — closed.
- **UJ protagonists:** all three named ("Boss") — closed.
- **Minor:** FR-22 says CLAUDE.md must stop promising an instincts engine; note the root CLAUDE.md's "Instincts" bullet and `src/instincts/engine.ts` table row describe the archived v1 TS app — FR-22 execution should scope the strike to the Python-relevant docs and clarify rather than blindly delete the v1-era architecture section.

---

**Verdict: READY-WITH-FIXES** — 1 high (FR-9 spec gap), 2 medium (soak criterion, jargon glossary), rest low. All fixes are sub-hour PRD edits; no structural rework needed.
