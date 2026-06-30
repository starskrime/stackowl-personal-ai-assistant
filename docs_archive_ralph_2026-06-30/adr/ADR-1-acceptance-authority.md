# ADR-1 — AcceptanceAuthority: every effectful action declares an observable post-condition

- **Status:** Proposed
- **Theme:** T1 (keystone). Closes/strengthens F-1, F-11, F-12, F-13, F-14, F-15, F-20, F-23, F-25,
  F-29, F-30, F-31, F-32, F-33, F-34, F-75, F-80, F-81, F-82, F-83; ⤷F-10.
- **Depends on:** nothing (it is the root). Prerequisite for ADR-2, ADR-5, ADR-6, ADR-7.

## Context
"Success" in StackOwl is asserted by the actor (`returncode==0`, "backend returned non-str", "no
exception"), never measured against the intended effect, and "did it work / deliver / overclaim?" is
re-decided by ≥6 disjoint proxies. B1–B4 began the fix (`ToolResult.verified`, `verify_artifact`,
`is_trustworthy_success` at `tools/verification.py:62`, the goal-level `AcceptanceChecker` at
`pipeline/acceptance.py:208`) but verification is opt-in-per-tool, file-only, and the checker is wired
only into `objectives/driver.py`. Directives honored: **verification > representation**;
**nothing removed** (the proxies are kept, made into delegators); positive-only learning (this ADR is
what *makes* the success signal trustworthy for T5).

## Decision
Introduce one **`AcceptanceAuthority`** and a first-class **`PostCondition`** contract.
- Every effectful action (tool, delivery, CLI mutation, provider generation, objective sub-goal)
  **declares** a `PostCondition`: a typed, observable assertion about reality after the act
  (`FileFresh(path)`, `HttpOk(url)`, `RowExists(query)`, `DeliveryAck(channel,msg_id)`,
  `NonEmptyText`, `Custom(probe)`).
- After the act, the authority **observes reality** (runs the probe) and returns a `Verdict`
  (`accepted: True | False | None` + reason + evidence). The actor never sets its own success.
- `ToolResult.success`/`verified` become **derived** from the authority's verdict via
  `is_trustworthy_success`, which already exists and already has the tri-state semantics.
- The 6 proxies **delegate**: `giveup_floor`, `overclaim_gate`, `judge_delivery`, per-tool `verify()`,
  objectives `AcceptanceChecker`, and the `side_effect_committed`/progress ledger all read the *same*
  `Verdict` instead of re-deriving one. They keep their distinct *responses* (floor text, gate, audit)
  but stop re-computing the *truth*.

## Why this, not the alternatives
1. *Keep the 6 proxies, just fix each gap (the S1–S4 approach).* Rejected: that is patching — it
   leaves N truths with N gaps and the next effectful tool re-introduces the bug. Provably incomplete
   (the audit found gaps in every proxy).
2. *Make `verify()` mandatory per tool but keep it tool-local.* Rejected: tools can still self-stamp
   (`F-25`), there's no authority over non-tool effects (delivery, CLI, provider), and "did the goal
   succeed" still isn't owned anywhere.
3. *LLM-judge everything (extend `judge_delivery`).* Rejected: a judge reading draft text is still
   representation, not observation; it fails open (F-15). Judges are allowed only as the `Custom`
   probe of last resort, never the default.
The powerful-machine context removes the original reason several effects were left unverified
(latency of a read-back / re-probe): default-ON observation is now correct.

## Shape
- `PostCondition` (new value type) + `Verdict` (extends today's tri-state). Actions attach a
  `PostCondition` the way they attach `capability_tag` today.
- `AcceptanceAuthority.observe(action, result) -> Verdict` sits at the **one** seam every effect already
  passes through: `Tool.__call__` (`tools/base.py:181` verify seam — generalize it), the delivery seam
  (`ProactiveDeliverer`/channel adapters), the CLI mutation helpers, and the objective sub-goal exit.
- Subsumes by delegation: `acceptance.py` `check()` becomes one probe family; `judge_delivery` becomes
  the `DeliveryAck`/`Custom` probe; `is_trustworthy_success` becomes the authority's output adapter;
  `giveup_floor`/`overclaim_gate` read `Verdict.accepted is False` instead of their own heuristics.

## Invariant established
**No action reports success for a declared effect without a `Verdict(accepted=True)` from an observation
distinct from the actor.** Equivalently: `success ⟺ measured`, never `success ⟸ asserted`. A tool that
self-stamps `verified=True` (F-25) is ignored — only the authority's verdict counts.

## Migration plan (flag-gated; default ON in production once verified)
1. Land `PostCondition`/`Verdict` + `AcceptanceAuthority` with a default `Custom`-noop probe → off =
   byte-identical (verdict = today's `is_trustworthy_success`).
2. Give the highest-blast effects real post-conditions first: send_file/send_message (`DeliveryAck` →
   closes F-29/30), shell (`FileFresh`/exit+effect → F-31), web_fetch (`HttpOk` → F-32), MCP
   (`NonEmptyText`/error → F-82/83), providers (`NonEmptyText` → F-20/23), write/media (`FileFresh`+
   content → F-33/34), CLI (`is_connected`/read-back → F-80/81).
3. Route the proxies to the verdict (closes F-15 fail-open, F-1/11/13/14 normal-turn acceptance,
   F-12 non-file kinds, F-25 self-stamp, F-75 spin).
4. No proxy and no learned-data column is deleted; each is rewired to delegate.

## Verification
- An invariant test: for a representative effectful action of each kind, force the effect to *not*
  happen (mock the side-effect to fail) and assert `success=False` — proving success tracks reality.
- A "no self-stamp" test: a tool returning `verified=True` with a failing post-condition still yields
  `accepted=False`.
- Live: trigger a failed Telegram send with the box down → the turn reports failure, not delivery.

## Blast radius, risk, rollback
Touches the success seam of every effect — highest blast radius of all ADRs, hence flag-gated with
off=identical. Risk: an over-strict probe turns a real success into a false failure (mitigated: probe
returns `None`=no-opinion, which T2 treats as "verify-or-ask," never as a hard fail). Rollback: flag off.

## Effort & dependencies
**XL.** Foundational — ADR-2/5/6/7 consume its `Verdict`. Build first.
