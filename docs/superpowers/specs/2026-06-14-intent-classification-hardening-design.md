# Design Spec — Intent-Classification Hardening + Graceful Bare-Timeout Floor

**Date:** 2026-06-14 · **Branch:** `feat/intent-classification-hardening` off `main` · **Theme:** reliability spine — stop a social message from spinning into a nonsensical give-up. Sibling of the (paused) per-model-context-budget slice; the answer-quality judge + lean charter remain the other arc slices.
**Status:** approved design (brainstorming gate) → feeds implementation plan.
**Regression gate:** full `tests/journeys/` green + existing `tests/owls/` router tests + `tests/pipeline/` supervisor/self-heal tests green.

## Origin (live failure, 2026-06-14 logs, trace `ebe9d681`)

The user sent the compliment **"i liked your message style to me."** The router classified it `intent_class=standard` (not conversational), so the conversational bypass did not engage; the turn entered the tool loop with 67 tools, the weak model hallucinated tasks ("durably save preference", "activate the Jarvis Protocol"), the persistence judge ruled give-up because no tools were used, the turn was nudged repeatedly, spun **135s**, hit the 120s default time backstop, and delivered a give-up floor with **blank** `capability that failed:` / `What I tried:` fields plus a leaked `Technical detail: budget cap reached: time limit=120.0 actual=135.2`. A friendly message produced a garbled give-up.

## Why it happened (grounded in `router.py` + `supervisor.py`)

1. **Classification is piggybacked on routing.** `SecretaryRouter._build_prompt` asks the fast model for the owl name (line 1) AND `conversational`/`standard` (line 2) in one routing-centric call with `_ROUTING_MAX_TOKENS=32`. `_parse_intent_class` reads ONLY line 2 and fail-safes to `standard`. A weak model fixated on the owl name drops/garbles line 2 → every miss becomes `standard`. The definition is also narrow ("only a greeting or small-talk with no task") — a compliment isn't obviously covered.
2. **The floor template is capability-shaped.** `synthesize_floor` (supervisor.py:147) always renders `localize_format("self_heal_floor", ...)` = *"I couldn't fully complete this: {goal}. The capability that failed: {failed_capability}. What I tried: {attempts}. {partial} Technical detail: {error}"*. For a bare timeout with no failed tool, `failed_capability`/`attempts`/`partial` are empty and `error` is the raw budget string → blank fields + a leaked internal detail.

## Goal

Make a purely social message reliably classify `conversational` (zero tools, instant reply) via the existing single routing call; and make any residual give-up that has no real capability data deliver a graceful, honest, jargon-free message instead of the blank capability template.

### Decisions (locked in brainstorming)
- **Improve the piggybacked line-2 only** (no separate classifier call, no execute-layer safety net — user choice): broaden the definition, scan ALL reply lines for the class token, raise the token cap. Fail-safe stays `standard`.
- **Graceful floor for no-capability stops:** when the floor has no failed capability AND no attempts AND no partial, deliver a warm honest fallback (new localize key), never the capability template; never leak the raw budget/error string to the user.
- **Accepted tradeoff (stated to the user):** improving line-2 *reduces* but cannot *eliminate* misclassification on a weak model; the graceful floor is the UX backstop so a residual miss still ends with a clean human reply.

### Non-goals
- A dedicated binary classifier call / an execute-layer "no-task → deliver plain draft" safety net (considered; not chosen this slice).
- Turn-language i18n of the floor (the floor `lang` stays `"en"` at the call site per the existing deferred i18n slice; the new key still ships en/de/fr/es entries for when that lands).
- The per-model tool budget (paused sibling slice), the answer-quality judge, the charter redesign.
- No DB / migration.

## Architecture

### A. Router classification hardening (`src/stackowl/owls/router.py`)
- **Broaden the definition** in `_build_prompt`'s line-2 instruction: `conversational` = the user is ONLY being social — a greeting, thanks, a compliment, an opinion/reaction, or chit-chat — with NO request to do, find, make, change, or look up anything; otherwise `standard`. Described by meaning (multilingual; no keyword lists in code).
- **Scan all lines** in `_parse_intent_class`: examine every line AFTER the owl-name line (`lines[1:]`) for a token that, stripped + lowercased, equals `conversational` or `standard`; take the first such match; fail-safe `standard` when none found. (Owl parse — line 1 — unchanged.)
- **Raise `_ROUTING_MAX_TOKENS`** 32 → 64 so the class line isn't truncated.
- Fail-safe direction unchanged (`standard`).

### B. Graceful bare-timeout floor (`src/stackowl/pipeline/supervisor.py` + `src/stackowl/setup/localize.py`)
- **New localize key** `self_heal_floor_graceful` (en/de/fr/es), a warm honest no-jargon message, e.g. en: *"Sorry — I got tangled up working on that and didn't finish cleanly. Could you tell me a bit more, or say it another way?"* No slots → nothing internal can leak.
- **`synthesize_floor` branch:** when `failed_capability` (after derivation) is empty AND `attempts` is empty AND `partial` is empty → return `localize("self_heal_floor_graceful", lang)` instead of the `self_heal_floor` capability template. The capability template is kept for genuine capability failures (populated fields). The except-path minimal fallback is unchanged.
- **Stop leaking the budget error:** the default-backstop empty-partial `BudgetBreach` path in `execute.py` (which calls the floor) no longer routes the raw `str(exc)` ("budget cap reached: time…") into the user-facing floor. With the graceful branch (no `{error}` slot) this is already neutralized; additionally the call site passes `error=None` for that path so observability keeps the marker in `state.errors`/logs while the user sees only the graceful message.

## Invariants
1. **Social → conversational:** a clear social message (greeting/thanks/compliment/opinion/chit-chat, any language) classifies `conversational` via the single routing call.
2. **Owl routing unchanged:** the class change never alters owl selection (line-1 parse byte-identical); existing routing tests stay green.
3. **Fail-safe preserved:** a missing/garbled class → `standard` (a misclassified task is never tool-stripped).
4. **No garbled floor:** a give-up with no failed capability + no attempts + no partial delivers the graceful message — never blank `capability that failed:`/`What I tried:` and never a leaked `budget cap reached`/raw error string.
5. **Capability floor intact:** a genuine consequential-capability give-up still names the real failed capability + detail (the existing template, unchanged for that case).
6. Language-agnostic detection (no English keyword lists in code); no silent excepts; `synthesize_floor` still never raises / never empty.

## Functional requirements (Given/When/Then)
- **FR1 (compliment → conversational):** *Given* "i liked your message style" (and other social phrasings) and a router reply containing the `conversational` token on any line, *when* the router parses, *then* `intent_class == "conversational"`.
- **FR2 (scan-all-lines):** *Given* a router reply where the class token is on line 3 (not line 2) or after extra whitespace, *when* `_parse_intent_class` runs, *then* it still returns the right class; *given* no class token anywhere, *then* `standard`.
- **FR3 (routing unchanged):** *Given* the broadened prompt, *when* owl selection runs over the routing test set, *then* the chosen owl is identical to before.
- **FR4 (graceful floor):** *Given* `synthesize_floor` with no failed_capability, no attempts, no partial (and any/empty error), *when* it renders, *then* it returns the graceful message — assert the blank-field capability phrasing and the raw error are ABSENT.
- **FR5 (capability floor intact):** *Given* a failed_capability + error, *when* `synthesize_floor` renders, *then* it returns the capability template naming them (unchanged).
- **FR6 (no leaked budget string):** *Given* a default-backstop empty-partial timeout, *when* the turn delivers, *then* the user text contains neither `budget cap reached` nor `Technical detail:` with the raw exception.
- **FR7 (zero regression):** full `tests/journeys/` + router + supervisor/self-heal tests green.

## Testing (gateway-driven where it crosses the pipeline; unit for pure parsing)
- **router units:** a reply `"secretary\nconversational"` → conversational; `"secretary\n\nconversational"` / class on line 3 → conversational (scan-all-lines); `"secretary"` only → standard (fail-safe); `"secretary\nstandard"` → standard; owl parse identical across all (FR1/FR2/FR3). Assert `_ROUTING_MAX_TOKENS == 64`.
- **synthesize_floor units:** no-data call → graceful message, asserting `"capability that failed" not in out` and `"budget cap reached" not in out` and a non-empty warm string (FR4); failed_capability+error call → capability template names them (FR5); the except-path minimal fallback still fires on interpolation error.
- **localize unit:** `self_heal_floor_graceful` present for en/de/fr/es; non-empty; no `{` slot leftovers.
- **gateway journey (the live-bug regression):** a turn whose scripted router reply marks the message `conversational` → drives the conversational bypass (zero tools, no tool loop, direct reply), NOT a 67-tool standard turn (FR1 end-to-end). A second journey: a default-backstop timeout with an empty partial → the delivered user text is the graceful message, with `budget cap reached`/`capability that failed` ABSENT (FR6).
- Full `tests/journeys/` regression (FR7).

## House rules
Strict mypy; 4-point logging; no silent excepts; no hardcoded English keyword lists for classification (the class comes from the LLM by meaning); reuse `localize`/`localize_format`; named constant for the token cap; no DB/migration; no vendor names in `src/`.

## Rollback
Additive/localized: revert the `_build_prompt` wording + `_parse_intent_class` scan + the `_ROUTING_MAX_TOKENS` bump (router returns to line-2-only); revert the `synthesize_floor` graceful branch + the new localize key + the `error=None` at the execute call site. The capability floor + owl routing are untouched.

## Composition note
Independent of the paused per-model-context-budget slice (different files: router/supervisor/localize vs presentation/registry/model_window; the only shared file is `execute.py`, and the two edits are in different regions — the budget-stop floor call site here vs the tool-schema budgeting there). Builds on the landed reliability spine ([[project_reliability_spine_backlog]]); complements the intent-gated conversational bypass it makes reachable. The deferred i18n slice will later thread the real turn language into the floor `lang` (today `"en"`).

## Verification constraint
Unit + gateway tests gate this. Live verification (send a compliment to @StackOwlbot, confirm a fast conversational reply + a clean message on any residual timeout) is deferred until the model box is reachable or a local model is pulled.
