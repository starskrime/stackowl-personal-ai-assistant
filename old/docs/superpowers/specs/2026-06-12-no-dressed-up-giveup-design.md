# Design Spec — No Dressed-Up Give-Up (Severity-Aware Honesty Floor)

**Date:** 2026-06-12 · **Branch:** `feat/no-excuse-delivery` (off `feat/agentic-os-stage1` @ e93fc80) · **Theme:** reliability spine — pillar ② self-healing + the trustworthiness invariant ("never dress a give-up as delivery").
**Status:** approved design (brainstorming gate) → feeds implementation plan.
**Regression gate:** full `tests/journeys/` suite green (currently 93 passed / 1 skipped).
**Origin:** the live assistant, asked to send something, ran an improvised SMTP shell script, the send failed (blocked port), and it replied *"I cannot deliver via the network… but I have built the full agentic bridge for you"* — a give-up dressed as delivery, handing the task back. This violates the user's core principles (deliver the result; verify OUTCOMES not tool names; self-extend or escalate honestly; never hand back manual steps).

## Why the existing self-heal missed it

The structural veto (`apply_structural_veto` → `is_structural_giveup`) only fires when `tool_failures ≥ 1 AND successful_tool_calls == 0 AND the draft is structurally trivial` (`persistence.py:118`). The dressed-up give-up defeats all three: the improvised script "succeeded" at writing a file (`successful_tool_calls > 0`), and "I built the bridge for you" is substantive (not trivial). So the veto stays silent, the weak judge rules `delivered=true`, and the excuse ships. The tally (`supervisor.py:19-20`) counts every tool equally — it is **severity-blind**: a failed `consequential` action (the actual SMTP send) counts the same as a successful trivial `read`/`write`. `ToolManifest.action_severity` (`read`/`write`/`consequential`) and `ToolResult.success` already exist — the signal just ignores them.

## Goal

Guarantee a turn never ships a dressed-up give-up: it delivers the real consequential outcome, self-extends (`tool_build`), or shows an **honest escalation** — enforced by a deterministic floor when the model won't recover. Make the missing detection structural and severity-aware.

### Decisions (locked in brainstorming)
- **Guarantee:** never ship a dressed-up give-up; the existing never-empty floor REPLACES the draft when detection fires and the (bounded) loop exhausts.
- **Detection (i) — structural, severity-aware (the hard guarantee):** a give-up when a `consequential`/`write` action was attempted and FAILED this turn AND no `consequential`/`write` action SUCCEEDED (the user's consequential outcome was not achieved) — regardless of trivial successes or draft substance.
- **Detection (ii) — hardened judge (best-effort):** the give-up judge prompt explicitly classifies "provides manual steps / 'I built it for you' instead of doing it" as `delivered=false`; floor as backstop when the judge catches it.
- **Recovery first, floor on exhaustion:** on detection, inject a capability-gap directive (self-extend OR honest-escalate, forbid hand-back); the (bounded — `[[bounded-turn-guarantee]]`) loop continues; on exhaustion the floor replaces.
- **Accepted tradeoff:** on a detected consequential-give-up the floor REPLACES the draft even if that draft was honest (we can't tell honest-report from dressed-up-claim structurally; "always honest" > "sometimes deceived").

### Non-goals
- Building an email tool (the triggering example) — the assistant SELF-EXTENDS via `tool_build`; we fix the global behavior, not the example.
- Request-intent classification ("did the request imply a consequential outcome") — out of scope; the signal keys off attempted-and-failed consequential actions, not predicted intent.
- No DB/migration. No `PipelineState` schema change beyond what tool records already carry.

## Architecture

### A. Severity-aware tool-call records (the plumbing)
Tool-call records that flow to the supervisor/veto (the `all_calls`/`tool_call_records` dicts — currently `{name, failed, ...}`) must also carry `action_severity`. At the dispatch site in `execute.py` (where `t.manifest.action_severity` is already in hand — ~line 576/618 and where records are appended), stamp `"action_severity": t.manifest.action_severity` into each record. Substitution siblings (`_try_substitute`) likewise record their severity. Additive; existing readers ignore the new key.

### B. Severity-aware tally + new signal (`supervisor.py` / `persistence.py`)
- Add `tally_consequential_outcomes(all_calls) -> (cons_failures, cons_successes)` counting only records whose `action_severity in {"consequential","write"}`. (`write` included: a failed write is also an unachieved effect.)
- Add `is_unachieved_consequential_giveup(*, cons_failures: int, cons_successes: int) -> bool` → `cons_failures >= 1 and cons_successes == 0`. Language-agnostic, draft-independent, severity-aware. A consequential SUCCESS defeats it (no false positive when the model genuinely did it).

### C. Veto fires on either signal, with the right directive (`apply_structural_veto`)
After the existing zombie check, add the consequential check. When the consequential signal fires, return the new `CAPABILITY_GAP_DIRECTIVE` (not the generic `PERSISTENCE_DIRECTIVE`). Precedence: an explicit judge directive still wins (kept); then zombie; then unachieved-consequential. Both directives flow through `decide_nudge` → injected as a nudge → loop continues.

### D. `CAPABILITY_GAP_DIRECTIVE` (new constant, `persistence.py`)
A global, example-free directive: *"A consequential action you attempted failed and is NOT done. Do ONE of: (a) build the missing capability with the tool_build tool and use it; (b) retry the outcome via a working capability; (c) state plainly that you could not do it and exactly why (the specific blocker). Do NOT give the user manual steps to do it themselves, and do NOT claim it is done or 'built' when it is not."* English (a prompt, like `PERSISTENCE_DIRECTIVE`); detection stays language-agnostic.

### E. Honest floor on exhaustion (reuse + enrich)
When the bounded loop exhausts (`decide_nudge` budget/nudge-ceiling → no directive → accept), the never-empty floor already backstops. Enrich the floor-from-calls path (`synthesize_from_calls`) to prefer the failed **consequential** tool as `failed_capability` and its error as `error`, so the floor reads "I attempted <capability> but it failed: <error>; I could not complete it." The floor REPLACES the (untrusted) draft on a detected consequential-give-up — wire it so a consequential-give-up turn that reaches terminal without a consequential success delivers the floor, not the draft. (Compose with the existing critical-failure/floor cascade; ensure the consequential-give-up path is treated as "no usable response" for the cascade.)

### F. Hardened give-up judge prompt (variant ii, `persistence.py:_build_messages`)
Add to the GAVE-UP (delivered=false) shapes: "hands the task back — gives the user manual steps/instructions to do it themselves, or claims to have 'built'/'set up' something for the user instead of performing the requested action." Best-effort (weak judge); the structural floor is the real guarantee.

## Honesty/safety invariants
1. A consequential action attempted-and-failed with no consequential success → detected structurally, regardless of draft confidence or trivial successes.
2. A consequential SUCCESS this turn → not a give-up (no false positive).
3. On detection: recovery directive first (self-extend/honest-escalate, hand-back forbidden); honest floor REPLACES on exhaustion.
4. The floor names the real failed capability + error (no generic hand-wave).
5. Detection is language-agnostic (severity + success bools); only the directive/judge prose is English.
6. No silent excepts; reuses the shipped veto/floor/`tool_build`.

## Functional requirements (Given/When/Then)
- **FR1 (detect):** *Given* a turn where a `consequential` tool fails and a trivial tool succeeds and the draft confidently claims success/hands back, *when* the veto runs, *then* `is_unachieved_consequential_giveup` fires and a directive is injected (the excuse is NOT accepted as delivery).
- **FR2 (no false positive):** *Given* a turn where a `consequential` tool SUCCEEDS, *when* the veto runs, *then* no consequential-give-up is signalled.
- **FR3 (recovery directive):** *Given* the consequential-give-up fires, *when* the nudge is issued, *then* it is the `CAPABILITY_GAP_DIRECTIVE` (steers to tool_build / retry / honest blocker; forbids hand-back).
- **FR4 (honest floor):** *Given* the loop exhausts without a consequential success, *when* the turn finalizes, *then* the user receives the honest floor naming the failed capability + error — NOT the dressed-up draft.
- **FR5 (judge hand-back):** *Given* a draft that hands the task back / claims "built it for you", *when* the give-up judge runs, *then* it rules `delivered=false` (best-effort).
- **FR6 (severity threaded):** tool-call records carry `action_severity`; the tally distinguishes consequential/write from read.
- **FR7 (zero regression):** full `tests/journeys/` green (existing self-heal/floor/substitution behavior intact).

## Testing (gateway-driven, provider-mock-only)
- `tally`/signal units: records with mixed severities → `is_unachieved_consequential_giveup` true when a consequential failed + none succeeded; false when a consequential succeeded; false when only reads failed.
- `apply_structural_veto` unit: consequential-give-up (failed consequential + successful trivial + substantive draft) → returns `CAPABILITY_GAP_DIRECTIVE` (the case that currently returns None).
- floor unit: `synthesize_from_calls` with a failed consequential record → floor names that capability + error.
- judge-prompt unit: the hand-back shape is described in the GAVE-UP section.
- **Gateway journey (the live-bug regression):** a scripted provider that calls a `consequential` tool which FAILS, then drafts "I built the bridge for you / here are the steps", and never achieves a consequential success → assert the delivered user text is the honest floor (names the failed capability), NOT the dressed-up claim; and a `tool_build`/retry path was offered (directive injected). A control: a consequential tool SUCCEEDS → normal delivery, no floor.
- Full `tests/journeys/` regression.

## House rules
Strict mypy; 4-point logging; no silent excepts; detection language-agnostic (no English keyword match on user/model text — severity+success bools only); reuse veto/floor/`tool_build`/`decide_nudge`. Named directive constant. No DB/migration.

## Rollback
Additive: revert the severity stamping in records, the new tally+signal, the veto branch + `CAPABILITY_GAP_DIRECTIVE`, the floor-from-calls consequential preference, and the judge-prompt addition. The existing zombie veto + floor are untouched.

## Composition / dependency note
Best paired with the bounded-turn-guarantee slice (paused) so the loop terminates promptly before the floor; without it, the floor still fires at the existing iteration cap. Flag for sequencing at merge.
