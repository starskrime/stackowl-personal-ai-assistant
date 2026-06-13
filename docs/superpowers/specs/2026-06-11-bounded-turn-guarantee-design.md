# Design Spec — Bounded-Turn Guarantee

**Date:** 2026-06-11 · **Branch:** new slice off `feat/agentic-os-stage1` · **Theme:** reliability spine — pillar ② self-healing (every turn terminates with a reply in bounded time).
**Status:** approved design (brainstorming gate) → feeds implementation plan.
**Regression gate:** full `tests/journeys/` suite green (currently 91 passed / 1 skipped).
**Builds on:** the Self-Healing Turn Supervisor (`decide_nudge`, the never-empty floor) and the existing `BudgetGovernor` machinery.

## Problem (found by a live run)

A real "hi" to the Telegram bot spiraled for **~11.5 minutes / 14 nudges** before replying, terminating only at the hard `tool_max_iterations=30` cap. Root cause (verified in code + live):
1. **`decide_nudge` escalation-reward never exhausts** (`supervisor.py:90-91`): if the model made any new tool call since the last nudge (`len(all_calls) > calls_at_last_nudge`), the nudge budget is NOT decremented. A weak model that calls a frivolous tool (`todo`/`update_plan`/`web_search`) every round is "rewarded" forever → infinite nudging until the iteration cap.
2. **No per-turn wall-clock/step bound by default** (`execute.py:687`): the `BudgetGovernor` is only created when an owl sets explicit `max_time_s`/`max_steps`/`max_cost_usd` — all default `None` (`bounds.py:56-58`), so an ordinary turn has no time/step bound; the only backstop is 30 iterations (~15–30 min on a slow local model).

Net: a tool-spamming weak model produces no reply for many minutes. Mock-provider journeys never exposed this; the live run did.

## Goal

Guarantee every turn terminates with a delivered reply (best-available, floor if none) in **bounded time/steps**, even when a weak model spirals — without changing happy-path behavior for normal turns.

### Decisions (locked in brainstorming)
- **Both backstops:** a default wall-clock/step deadline AND a `decide_nudge` absolute ceiling.
- **On breach: deliver best-available; floor if nothing usable** (reuse the shipped never-empty floor). No extra "cut-short" note (choice A) → the default-backstop path SUPPRESSES the existing `budget cap` marker.
- **The default backstop STOPS + delivers — no interactive "Raise?" prompt** (the Raise UX stays for explicitly-configured owl caps).
- Reuse the existing `BudgetGovernor`/`make_budget_callback`/floor — minimal new code.

### Non-goals
- No change to explicitly-configured owl `ResourceCaps` behavior (they keep the interactive Raise + their marker).
- No capability-probing of the deadline (fixed sensible default; an owl can override via its caps).
- No change to `tool_max_iterations=30` (it remains the final hard backstop).

## Architecture

### A. Default backstop caps — activate the dormant `BudgetGovernor` (`execute.py` ~678-700)
New named constants (in `authz/bounds.py` or a config module, NOT magic numbers):
- `DEFAULT_TURN_MAX_TIME_S = 120.0` — generous for happy-path multi-step (≈3–4 slow-model calls), bounds the spiral.
- `DEFAULT_TURN_MAX_STEPS = 20` — deterministic, host-independent backstop below the 30 hard cap.

At the caps-resolution site: after computing `_caps` (line 678-681), if the owl set **no** explicit caps (`_has_caps` is False), substitute a **default backstop** `ResourceCaps(max_time_s=DEFAULT_TURN_MAX_TIME_S, max_steps=DEFAULT_TURN_MAX_STEPS)` and set `_default_backstop = True`. This makes `_has_caps` True → the `BudgetGovernor` always runs. When the owl DID set explicit caps, leave them untouched and `_default_backstop = False`.

The `BudgetGovernor.check()` already returns a `BudgetBreach("time"/"steps", …)` after the breaching iteration; `make_budget_callback` raises it carrying the partial; the loop stops and the partial is delivered (proven by `test_budget_cap`). We reuse this verbatim.

### B. Suppress the Raise prompt + marker for the default backstop (`make_budget_callback` call site + marker site)
- Build the budget callback with **`interactive=False`** when `_default_backstop` is True (the callback's `interactive` param locally gates ONLY the budget Raise/Stop clarify round-trip — confirmed in `callback.py`; it does not affect the turn's real interactivity). For explicit caps, pass `interactive=state.interactive` (unchanged).
- The existing breach path injects a `budget cap`/`stopped` marker onto the partial. For the **default backstop**, suppress that marker (deliver clean best-available; floor if empty — choice A). The plan locates where the marker text is added (the breach handler in `execute.py`/the backend) and gates it on `not _default_backstop`. Explicit caps keep their marker.

### C. `decide_nudge` absolute ceiling (`supervisor.py`)
Add an absolute per-turn nudge ceiling: escalation still waives the per-nudge **cost** (budget not decremented on escalation), but once the **total nudges issued** reaches `MAX_TURN_NUDGES = 6`, `decide_nudge` returns no directive (stop) regardless of escalation/budget. The provider loop (`openai_provider.py`/`anthropic_provider.py`) already holds nudge state as nonlocals (`nudge_budget`, `calls_at_last_nudge`); add a `nudges_issued` nonlocal incremented per issued directive, and pass it (+ the ceiling) to `decide_nudge`, which enforces `if nudges_issued >= MAX_TURN_NUDGES: return None`. This stops the spam-spiral EARLY (≈6 rounds) independent of the time/step bound. Shared by both providers via the existing `decide_nudge` (DRY).

On the no-directive return, the loop falls through and delivers best-available (existing behavior).

## Honesty/safety invariants
1. Happy-path turns unchanged: a normal turn completing well within 120s / 20 steps / 6 nudges sees identical behavior (governor never trips, ceiling never hit).
2. Every turn delivers: on any backstop trip → best-available partial, or the never-empty floor if no usable content. No silent empty turn.
3. The default backstop never nags the user (no Raise prompt) and adds no marker (choice A); explicit owl caps keep their Raise + marker.
4. No silent excepts; breach handling is the existing logged path.
5. Bounded by construction: a tool-spamming model hits the nudge ceiling (~6) or the time/step bound — it can no longer run to the 30-iteration cap.

## Functional requirements (Given/When/Then)
- **FR1 (nudge ceiling):** *Given* a model that makes a new tool call every round (continuous escalation), *when* it reaches `MAX_TURN_NUDGES` nudges, *then* `decide_nudge` stops nudging and the loop delivers — it does NOT nudge indefinitely.
- **FR2 (time/step backstop):** *Given* a turn with no explicit owl caps, *when* it exceeds `DEFAULT_TURN_MAX_TIME_S` or `DEFAULT_TURN_MAX_STEPS`, *then* the `BudgetGovernor` stops the loop and a reply is delivered.
- **FR3 (best-available / floor):** *Given* a backstop trips, *when* the turn ends, *then* the user receives the best-available partial, or the never-empty floor if no usable content.
- **FR4 (no Raise/marker on default backstop):** *Given* the default backstop trips on an interactive turn, *when* it stops, *then* NO "Raise the cap?" prompt is shown and NO `budget cap` marker is added.
- **FR5 (happy path unchanged):** *Given* a normal turn well within all bounds, *when* it runs, *then* behavior is identical to today (no governor trip, no ceiling, no extra prompt/marker).
- **FR6 (explicit caps unchanged):** *Given* an owl with explicit `ResourceCaps`, *when* a cap trips, *then* the existing Raise prompt + marker behavior is unchanged.
- **FR7 (zero regression):** full `tests/journeys/` stays green.

## Testing (gateway-driven, provider-mock-only)
- `decide_nudge` unit: a scripted sequence with continuous escalation (`current` always > `calls_at_last_nudge`) → assert it returns no directive at the `MAX_TURN_NUDGES`-th call (ceiling fires despite escalation); and that below the ceiling escalation still waives the budget cost.
- Governor/time backstop: extend the `test_budget_cap` pattern to `max_time_s` (manual clock advanced past the default) → loop stops + partial delivered.
- Default-backstop wiring (execute): a turn with no explicit caps → `BudgetGovernor` is instantiated (was None before); on trip the callback was built with `interactive=False` (no Raise) and no marker added.
- Gateway journey: a scripted provider that tool-spams forever (new tool call every round, never delivers) → the turn TERMINATES with a delivered reply within the bound (assert it stopped well under 30 iterations, e.g. ≤ the nudge ceiling rounds, and `state.responses` is non-empty). This is the live-bug regression test.
- FR5 happy-path journey: a normal scripted turn that answers in 1-2 iterations → no governor instantiation trip, no ceiling, identical delivery.
- Full `tests/journeys/` regression (FR7).

## House rules
Strict mypy; 4-point logging on the new backstop branch + the ceiling; no silent excepts; named constants (no magic numbers); reuse `BudgetGovernor`/`make_budget_callback`/floor/`decide_nudge`. No DB/migration.

## Rollback
Mostly additive/config: revert the default-backstop substitution in `execute.py` (caps fall back to all-None → governor dormant as before), the `interactive=False`/marker-suppression gating, and the `decide_nudge` ceiling param. `tool_max_iterations=30` and explicit-cap behavior are untouched throughout.

## Open values to confirm at spec review
`DEFAULT_TURN_MAX_TIME_S=120.0`, `DEFAULT_TURN_MAX_STEPS=20`, `MAX_TURN_NUDGES=6` — sensible defaults; adjust if you have a preferred user-facing reply-time bound.
