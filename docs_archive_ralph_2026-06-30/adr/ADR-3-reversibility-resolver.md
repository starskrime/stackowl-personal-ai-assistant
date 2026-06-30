# ADR-3 — ReversibilityResolver: a first-class stakes/reversibility signal + default resolution

- **Status:** Proposed
- **Theme:** T3. Closes/strengthens F-3, F-27, F-44, F-56, F-68, F-69, F-70, F-71.
- **Depends on:** none hard; composes with ADR-1 (reversibility of an effect is known where its
  post-condition is declared).

## Context
Clarify/consent/cost-pause/router each re-decide "ask the human or act?" with no shared signal, so each
defaults to the passive "ask" — structurally capping proactivity (the "Jarvis" goal). The act-first
rule exists only as prose in `dna_injector`/tool descriptions, never enforced. `consent.py:203` defaults
to `ALWAYS_ASK`; `clarify.py:200` unconditionally parks. Directives: nothing removed (every gate still
exists and still parks the genuinely irreversible); no hardcoded lists (reversibility is declared per
action, not pattern-matched).

## Decision
Introduce one **`ReversibilityResolver`** plus a declared **`Reversibility`** signal on every action
(`reversible_via=<undo handle> | irreversible | unknown`) and a **stakes** estimate. A single
`resolve(decision) -> ACT_WITH_ASSUMPTION | ASK` authority sits in front of every gate: for a
**reversible, low-stakes** decision it returns "act on the most-likely interpretation, state the
assumption, keep the undo handle"; it parks **only** when irreversible-or-high-stakes. Gates
(clarify tool, clarify_gateway, cost_pause, consent, router clarify verdict, a2a) call the resolver
instead of unconditionally parking.

## Why this, not the alternatives
1. *Tune each gate's prompt to "act more" (the S2/S3 approach).* Rejected: prose isn't enforcement; a
   weak model still over-asks, and each gate drifts independently (F-3/44/68/70 are four gates with the
   same reflex).
2. *Global "auto-approve everything reversible" flag.* Rejected: reversibility must be *declared and
   checked* per action (an `undo_write` exists; a sent message does not), else "reversible" is a guess.
Powerful-machine context lets the resolver afford a cheap probe/LLM check of "most-likely
interpretation" before acting, rather than asking.

## Shape
- `Reversibility` declared alongside the action's `PostCondition` (ADR-1) — e.g. `write_file` →
  `reversible_via=undo_write`; `send_message` → `irreversible`.
- `ReversibilityResolver.resolve(decision, context) -> Verdict{act|ask, assumption, undo_handle}`.
- Subsumes by delegation: `consent.py` tiers gain a `reversible→AUTO_WITH_UNDO` tier (F-27);
  `clarify` tool/gateway call the resolver pre-park (F-68/69/71); `cost_pause` treats reversible spend
  under the hard cap as act-and-notify (F-70); router `clarify` verdict + a2a default-deny defer to the
  resolver (F-3/56/44). The human-ask path is untouched for the irreversible.

## Invariant established
**A reversible, low-stakes decision is resolved with a stated assumption + undo handle, never parked on
the human; only irreversible-or-high-stakes decisions reach the user.** "Ask" becomes the exception,
gated on declared irreversibility — not the default.

## Migration plan (flag-gated; default ON once verified)
1. Add `Reversibility` to the action contract; default `unknown` → resolver treats `unknown` as today
   (ask) → off = byte-identical.
2. Declare reversibility on reversible actions; flip the resolver on per-gate.
3. Each "acted-on-assumption" is recorded (ADR-7) so the user can audit/override.

## Verification
- For a reversible action with an obvious default, assert the turn proceeds + states the assumption +
  retains the undo handle; for an irreversible one, assert it parks.
- Live: an ambiguous-but-reversible request completes with "I assumed X (undo: …)" instead of a question.

## Blast radius, risk, rollback
Touches interaction gates; flag-gated. Risk: acting on a wrong assumption (mitigated: only when
reversible + undo handle retained + recorded). Rollback: flag off → all gates revert to ask.

## Effort & dependencies
**M.** Independent of ADR-1 but cleaner after it (reversibility co-declared with post-conditions).
