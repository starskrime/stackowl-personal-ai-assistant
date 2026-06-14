# Design Spec — Answer-Quality Judge (grade the answer, not the tool)

**Date:** 2026-06-14 · **Branch:** `feat/answer-quality-judge` off `main` · **Theme:** reliability spine — pillar ② self-healing / the persistence judge. The third root cause of the compliment→spin failure; sibling of the shipped intent-classification hardening and the paused per-model-context-budget slice.
**Status:** approved design (brainstorming gate) → feeds implementation plan.
**Regression gate:** full `tests/journeys/` green + existing `tests/pipeline/` persistence / self-heal / no-dressed-up-giveup tests green.

## Origin (live failure, trace `ebe9d681`)

The compliment "i liked your message style" was (mis)classified `standard`, entered the tool loop, and the model replied without using tools. The persistence judge ruled `delivered=false` — *"used zero tools with no evidence"* / *"claims a limitation but no command was run"* — which injected the persistence nudge, and the loop spun for 135s until the time backstop. The intent-classification slice now sends a *correctly*-classified social message down the zero-tool bypass (no judge), but a RESIDUAL misclassified social/no-task message — or any `standard` turn answerable from knowledge — still reaches the judge, which has no concept of "this needed no tool."

## Why the judge mis-fires (grounded in `persistence.py:_build_messages`)

Every DELIVERED and GAVE-UP criterion in the judge prompt is framed around a *task* and "THE UNIVERSAL ESCAPE HATCH" (run a command / install / build). DELIVERED requires "produced the requested outcome" or "stated a blocker AFTER trying the escape hatch"; GAVE-UP includes "refused/apologized/deferred WITHOUT exhausting capabilities" and "claims a limitation BUT used no command/install." There is **no criterion for a request that requires no tool at all** (a greeting, opinion, acknowledgement, or a question answerable from knowledge). So a correct tool-free reply is read as "refused without trying" → `delivered=false` → nudge → spin.

## Goal

Make the judge first decide whether the request needs an external action, and accept a real tool-free reply to a no-action request as delivery — WITHOUT weakening the dressed-up-giveup / lazy-refusal detection for action-requiring requests.

### Decisions (locked in brainstorming)
- **Gate the single judge prompt on "needs a tool?"** (no second call, no structural rewrite). First classify the request; apply the escape-hatch give-up logic ONLY to action-requiring requests.
- **Directly-answerable → delivered:** a real, on-point tool-free reply to a no-action request is DELIVERED; a tool-free reply is NOT evidence of give-up there.
- **Preserve the give-up logic verbatim** for action-requiring requests (claims-without-doing, refused-without-trying, technical-excuse-without-trying, hand-back).
- **Composition unchanged:** the structural veto (`is_consequential_giveup_now`, ledger severity) still fires for any real consequential failure regardless of the judge — so judge leniency on no-tool replies cannot re-open the dressed-up-giveup hole.

### Non-goals
- A second classifier call / removing the escape-hatch prose (considered; not chosen).
- Changing where/when the judge is invoked, the JSON schema, the nudge mechanism, or the structural veto.
- The paused per-model-context-budget slice; the model-aware charter slice.
- No DB / migration.

## Architecture

### Single change: reframe `_build_messages` (`src/stackowl/pipeline/persistence.py`)
Rewrite the system + user judge prompt content (only) to:
1. **Lead with the gate:** "FIRST decide whether fulfilling the request REQUIRES an external action — sending, creating, changing, running something, or fetching live/external data — OR is answerable directly from the conversation or your own knowledge (a greeting, thanks, a compliment, an opinion or reaction, chit-chat, an acknowledgement, or a question you can answer from what you know). Judge by meaning, in any language."
2. **Add a DELIVERED bullet:** "The request was directly answerable and the draft gives a real, on-point reply. Using NO tools is correct for such a request — a tool-free reply to a no-action request is NOT a give-up."
3. **Scope the existing escape-hatch + GAVE-UP bullets** under an explicit heading "FOR A REQUEST THAT REQUIRES AN EXTERNAL ACTION:" — keep all four shapes verbatim (claims-something-done-but-tool-failed; refused/deferred without exhausting; technical-excuse-without-running-a-command; hands-the-task-back).
4. Keep the JSON schema instruction and the `{"delivered":…,"reason":…}` output unchanged. Keep it GLOBAL (no domain/language-specific content; no keyword lists).

`judge_delivery`, `summarize_tool_outcomes`, the caps, the nudge directive, and all call sites are UNCHANGED.

## Invariants
1. **No-action delivered:** a request that needs no external action + a real on-point tool-free draft → `delivered=true` (no nudge).
2. **Dressed-up give-up intact:** an action request whose draft claims it did something but the backing tool failed / no capable tool succeeded → `delivered=false` (unchanged).
3. **Lazy-refusal intact:** an action request refused/deferred without trying the escape hatch → `delivered=false` (unchanged).
4. **Hand-back intact:** "here are the steps / I built it for you" instead of doing an action task → `delivered=false` (unchanged).
5. **Structural veto composes:** a real consequential tool failure is still vetoed regardless of the judge verdict.
6. Language-agnostic; no English keyword lists; judge stays fail-OPEN on provider/parse error (unchanged); the floor/structural layers remain the hard guarantees.

## Functional requirements (Given/When/Then)
- **FR1 (no-action delivered):** *Given* a social/opinion/acknowledgement message (or a knowledge question) and a real tool-free draft with no tools used, *when* the judge runs with the reframed prompt, *then* the verdict is `delivered=true` (the prompt's directly-answerable path applies). [Prompt-content asserted in unit; real-model behavior is live-verified.]
- **FR2 (dressed-up give-up still caught):** *Given* an action request whose draft claims success but the tool is `failed`, *when* the judge runs, *then* `delivered=false`.
- **FR3 (prompt content):** the reframed prompt contains the needs-an-external-action gate, the directly-answerable=delivered criterion, and all four action-request give-up shapes.
- **FR4 (composition):** the structural veto + giveup-floor paths are unchanged (a real consequential failure is still vetoed/floored).
- **FR5 (zero regression):** full `tests/journeys/` + existing persistence/self-heal/no-dressed-up-giveup tests green.

## Testing (unit for prompt content + wiring; gateway for verdict→behavior; LLM verdict is live-verified)
- **prompt-content units** (`_build_messages`): assert the system prompt contains the gate (an "external action" notion AND an "answerable directly"/knowledge notion), the directly-answerable DELIVERED criterion, and each of the four action-request give-up shapes (claims-but-failed, refused-without-trying, technical-excuse, hand-back) — so the reframe ADDS the gate without dropping the dressed-up-giveup detection.
- **judge_delivery wiring unit:** with a scripted provider returning `{"delivered":true,...}` for a no-tool draft → `(True, reason)`; `{"delivered":false,...}` → `(False, reason)`; malformed/provider-error → fail-OPEN `(True, JUDGE_ERROR_REASON)` (unchanged behavior).
- **gateway journey:** a `standard` turn with a no-action message where the scripted model replies without tools and the scripted judge double (consistent with the reframed criteria) returns `delivered=true` → assert NO persistence nudge is injected and the reply is delivered (the verdict→no-nudge wiring). A control: an action turn whose draft claims success with a failed tool → the structural veto / give-up path still fires.
- **regression:** the existing no-dressed-up-giveup journey + self-heal invariant + persistence unit tests stay green (FR2/FR4/FR5).

## House rules
Strict mypy; 4-point logging; no silent excepts; the judge prompt stays GLOBAL + language-agnostic (no keyword lists, judged by meaning); reuse the existing `judge_delivery`/structural-veto/floor composition; no DB/migration; no vendor names.

## Rollback
Single-file, prompt-only: revert `_build_messages` to restore the prior judge prompt. No signature, schema, or wiring change to roll back.

## Composition note
Completes the trio of fixes for the compliment→spin failure: intent-classification hardening (don't enter the tool loop) + graceful floor (clean message if it does spin) + this (don't nudge a correct no-tool reply). Independent of the paused per-model-context-budget slice (different file). Builds on the landed self-healing supervisor + no-dressed-up-giveup structural veto ([[project_self_healing_supervisor]], [[project_reliability_spine_backlog]]).

## Verification constraint
Unit (prompt content + wiring) + gateway (verdict→behavior) tests gate this. Whether the reframed prompt makes a weak judge model actually rule a real "i liked your message style" as `delivered=true` is LIVE verification — deferred until the model box is reachable or a local model is pulled.
