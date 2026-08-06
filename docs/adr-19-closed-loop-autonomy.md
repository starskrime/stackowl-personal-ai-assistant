# ADR-19 — Closed-Loop Autonomy: the root concept for a self-healing, self-improving StackOwl

> **Status:** proposed — research + root concept, requested by Bakir 2026-08-05
> **Supersedes nothing.** Subsumes and gives a shared shape to ADR-6
> (`HealableResource`), the verification-primitive arc, the self-heal platform arc,
> and D02.6's recovery ladder.
> **Every number below was measured on this box on 2026-08-05**, against 15 days of
> production logs (`~/.stackowl/logs/stackowl*.jsonl`) and the live database.

## The ask

> *"you need to have some backend research and come with root concept for this
> platform to be self healing and self improving agent"*

## The finding, in one sentence

**StackOwl detects superbly, acts mechanically, and — in three of its four
autonomic loops — learns nothing.** The machinery is not missing; it is
three-quarters built and finished in exactly one place. The last quarter —
*feeding the outcome back so the next attempt differs* — exists only in the
circuit breaker, which is measurably the one loop that behaves correctly.

That single working example is the argument. The contract below is not imported
from the reference platform; it is generalised from the one part of StackOwl
that already satisfies it.

This is why "self-healing" and "self-improving" are not two projects. They are the
same control loop, and StackOwl is missing the same leg of it in both.

## The measurement

Four autonomic loops exist today. All four were measured end to end. One has a
feedback leg and works; three do not and don't.

### Loop 1 — provider health (the circuit breaker) — **CLOSED, and this is the proof**

> **Correction, 2026-08-05.** The first draft of this document called this loop
> open, on the grounds that "the probe is identical every time — no adaptive
> backoff, no memory of the previous 1,390 failures." **That was wrong**, and it
> was wrong in the specific way this whole ADR warns about: I read the outcome
> numbers and inferred the mechanism instead of reading the mechanism. The
> correction is kept in place rather than edited away, because a research
> document that quietly revises its own claims is worth less than one that shows
> which claims did not survive checking.

| stage | measured |
|---|---:|
| detections (short-circuit events) | **43,908** |
| threshold trips (`CLOSED → OPEN`) | 26 |
| recovery probes (`OPEN → HALF_OPEN`) | **1,401** |
| probe failures (`HALF_OPEN → OPEN`) | 1,391 |
| probe successes (`HALF_OPEN → CLOSED`) | 8 |

`FX-02` already built the feedback leg: `_current_half_open_seconds` **doubles on
each failed probe** and resets to base on success, capped at
`_HALF_OPEN_BACKOFF_CAP_SECONDS = 900.0`.

And the arithmetic confirms it is working, rather than merely present:

```
a provider OPEN continuously, probed at the 900s cap:
    15 days x 86,400s / 900s  =  1,440 probes
observed OPEN -> HALF_OPEN    =  1,401 probes
```

**A 2.7% gap.** The probe count is fully explained by a genuinely-unavailable
provider being probed at exactly the capped rate the design intends. The 0.57%
success rate is not a broken loop — it is an accurate measurement of an upstream
that was down for most of the window, and the loop closed 8 times when it wasn't.

**So this loop belongs in the ADR as the POSITIVE case.** It is the only one of
the four with a feedback leg, and it is the only one whose behaviour is correct.
That is the argument for the contract below, made from our own code rather than
from the reference platform's.

### Loop 2 — incident → RCA

| stage | measured |
|---|---:|
| incidents opened | 1,131 |
| RCA runs completed | 1,103 |
| **"RCA produced no verdict — NOT marking handled"** | **725** |
| "missing root_cause/fix — no verdict" | 410 |
| "a stage failed — no verdict" | 315 |
| "too few precisely-attributed rows to recur on" | 2,712 |

**64% of RCAs produce nothing.** The honesty is genuinely good — it refuses to
mark an incident handled without a verdict, which is the trust arc working. But a
loop that runs 1,103 times and yields a verdict 378 times is spending its budget
on the sensing half.

### Loop 3 — lesson → application — **the VERIFY leg, not the feedback leg**

> **Second correction, 2026-08-05.** The first draft read "2,680 lessons written,
> 31 retrieved, 1 applied" and concluded lessons were never fed back. **The
> retrieval count was a logging artifact.** `classify._gather_lessons` logs its
> entry and exit at `log.engine.debug`, and this deployment runs at INFO — so the
> 31 measured the log level, not the system. The same trap D02.6 documented for
> its `cause` field, hit again one day later.

Read from the code instead of inferred from counts:

* `classify.py:700` calls `_gather_lessons` **unconditionally, every turn**.
* The selector is deliberately query-INDEPENDENT — keyed on the owl's identity,
  not on what the user just typed — because D01.1 needed the injected block to be
  identical across a session for prompt-cache stability.
* INFO-level anchor for real volume: **2,193 `classify: exit`** and 2,839
  `assemble: exit` in the window.

| stage | measured |
|---|---:|
| lessons stored | 2,680 |
| turns that classified (INFO, reliable) | **2,193** |
| turns lessons were injected into | **all of them** (unconditional call) |
| **`note_applied_lesson` calls** | **1** |

**So the feedback leg exists.** Lessons are written into the prompt the agent
already reads — obligation ⑤ satisfied, and by the best available design.

What is missing is obligation ③: **nothing measures whether injecting them
changes anything.** The sole signal that a lesson mattered is a tool the model
must voluntarily call, and it called it once in fifteen days across 2,193 turns.
We are paying prompt tokens on every turn for a mechanism whose effect has never
been observed — not because it doesn't work, but because nothing looks.

That reframes the intervention completely. The work is not "inject lessons"
(done). It is "make injection falsifiable" — e.g. hold the block out on a sample
of turns and compare outcome quality, which the `task_outcomes` table already
records. A cheap, honest experiment beats a bigger mechanism built on faith.

### Loop 4 — skill creation → catalog health

| stage | measured |
|---|---:|
| skills in the catalog | **421** |
| skills ever executed | **33 (7.8%)** |
| total executions across all 421 | 208 |
| skills with a success rating | 27 |
| skills archived / retired / consolidated | **0** |

And the top of the usage table is the tell:

| skill | executions |
|---|---:|
| `structure-incident-evidence` | 45 |
| `structure-incident-evidence-brief` | 43 |
| `structure-evidence-brief` | 23 |
| `evidence-brief-structuring` | 13 |
| `dual-memory-evidence-gathering` | 11 |

**Four of the top five are near-duplicates of one another.** The write side of
self-improvement works so well that it has generated a catalog that is 92% dead
weight, in which the live 8% is largely the same skill written four times. Nothing
measures that, nothing prunes it, and every one of the 421 competes for space in
tool-search ranking and prompt assembly.

## The root concept: the Closed-Loop Contract

All four loops implement SENSE → DECIDE → ACT. Only the circuit breaker
implements VERIFY → LEARN → FEED BACK — and it is the only one that works. So
the root concept is not a new subsystem. It is a **contract that every autonomic
mechanism in StackOwl must satisfy**, generalised from the mechanism that already
does, plus a refusal to call anything "self-healing" until it satisfies it.

```
   ┌───────── SENSE ──────────┐        structural signal, never English-matched
   │                          ▼
   │                       DECIDE       a named cause with a prescribed action
   │                          │
   │                        ACT         an ACTUATOR that can change the outcome
   │                          │
   │                       VERIFY       measured, never asserted
   │                          │
   │                        LEARN       the outcome is WRITTEN somewhere
   │                          │
   └────────── FEED BACK ─────┘        ...and the next attempt READS it
                  │
                DECAY                   ...and unused knowledge ages out
```

### The five obligations

Any mechanism claiming to be self-healing or self-improving must declare all five.
A mechanism that cannot name its actuator is a diagnosis, not a recovery.

**① SIGNAL — structural, never linguistic.**
D02.6 established this for provider failures: classify by status code and
exception type, never by matching English in a message. **The platform violates
its own rule at the root of its healing primitive:**
`infra/resilience.py::looks_like_dead_handle` was a hardcoded list of 19 English
substrings (`"Connection closed"`, `"database is locked"`, `"Broken pipe"`…)
deciding whether a resource died — governing the retry path for EVERY tool and
the whole browser stack. The same design D02.6 refused to port from the reference
platform, sitting in our own ADR-6 foundation.

**SHIPPED 2026-08-05 (`DEBT-41`).** Type first, text last: `ConnectionError` and
`EOFError`, plus playwright/aiohttp/httpx/websockets probed lazily by (module,
attribute). The substring pass survives only because Playwright genuinely raises
a bare `Error` whose sole signal is its message — and every use of it now logs,
so our remaining dependence on the fragile path is measured rather than assumed.

A detail that makes the case better than argument did: while verifying a restart
the same day, a shutdown race raised `sqlite3.ProgrammingError("Cannot operate on
a closed database.")` — a string **not** in the markers. Healing fired only
because the chained `ValueError("Connection closed")` happened to contain one
that was. Luck, not classification.

**② ACTUATOR — something that can actually change the outcome.**
The lesson is one day old and expensive: D02.6 shipped `RecoveryAction.COMPRESS`
with nothing able to perform it, and it was filed as debt until Bakir named
self-healing as the guide star. A named recovery with no actuator is worse than
an unnamed one, because it reads as covered. **Rule: no cause enters a taxonomy
without an actuator, or an explicit test pinning that it has none.**

**③ VERIFICATION — measured, not asserted.**
Already solved, and it is the platform's genuine advantage: `ToolResult.verified`,
`AcceptanceChecker`, the grounding gate. The recovery loops must *use* it —
"the probe succeeded" has to mean an observed success, not a call that failed to
raise.

**④ FEEDBACK — the next attempt must differ.**
The missing leg in three of four. The breaker shows what it looks like when
present: `_current_half_open_seconds` doubles per failed probe and resets on
success, so a 30-second outage and a 6-hour one are not probed identically.
What the other three need:
- an RCA verdict must change the next turn, not wait for the model to volunteer;
- a lesson must be injected, not offered;
- a skill catalog must age, or its ranking signal drowns (shipped — see #1).

**Architectural principle, taken from the reference platform:** write the
improvement into **the artifact the agent already reads** — the skill, the
memory, the prompt — never into a side-channel store that requires voluntary
retrieval. StackOwl's lessons live in a store nothing reads (31 retrievals /
2,680 writes). The reference platform's post-turn review fork writes straight into
the skill and memory stores that are loaded on every turn by construction. That
single difference is why its loop closes and ours does not.

**⑤ DECAY — unused knowledge must age out.**
Without it a self-improving system poisons itself, which is measurably already
happening: 421 skills, 33 used, 4 of the top 5 duplicates. Decay is not cleanup;
it is what keeps the improvement signal legible.

**⑥ A LOOP MUST NOT DEPEND ON WHAT IT HEALS.**
Added 2026-08-05, from measurement rather than principle. During an LLM-backend
outage on 07-29 the platform attempted **1,294 turns against a normal day's
60-150**, and 871 of them were the RCA channel — invisible on a healthy day.

```
provider dies -> turns fail -> failures open incidents -> RCA runs to diagnose
   -> RCA's three stages call the SAME dead provider -> more failures -> ...
```

The healing machinery consumed the outage as input and multiplied it tenfold,
producing 258 barren verdicts. `AllProvidersUnavailableError` is an
`InfrastructureError`, so the classifier said "analyze" — a three-stage LLM
diagnosis of a condition whose entire content is *there is no model to think
with*.

**The rule: before acting, a loop must ask whether its own actuator depends on
the thing that failed.** If it does, the correct action is to WAIT, and waiting
must be logged — "we chose not to diagnose" cannot look like "there was nothing
to diagnose". Shipped as the `defer` outcome (`DEBT-48`).

This is the obligation with the sharpest teeth, because a loop that violates it
does its worst damage at exactly the moment the system is least able to absorb
it.

## What the reference platform does, and what we take

Read from `agent/background_review.py` (991 lines) and `agent/curator.py` (2,018).

| mechanism | theirs | ours today |
|---|---|---|
| post-turn reflection | fork the agent after **every** turn; whitelisted to memory+skill tools; writes directly to the stores the agent reads | none — a store nothing reads |
| catalog maintenance | curator: inactivity-triggered, `active → stale (30d) → archived (90d)` | none — 0 of 421 ever transitioned |
| restructuring | opt-in LLM consolidation pass merging near-duplicates | none |
| safety | **never auto-delete, only archive**; pinning is a human veto | n/a |
| cost discipline | the fork runs on its own prompt cache, never disturbing the live conversation | n/a |

**Adopt:** the closed-loop shape; write-into-the-read-artifact; deterministic
decay always on with LLM restructuring opt-in; never-delete-only-archive; pinning
as a human veto; usage counters as the lifecycle signal.

**Diverge:** we already *have* the usage signal (`skills.n_executions`,
`skills.success_rate`) — their curator derives it from file timestamps. Ours is
better and unused. And our decay must be driven by that measured signal rather
than by mtime.

**Do not adopt:** their English-substring error classification (D02.6 settled
this), and their learning *graph* — it is a desktop visualisation, not a control
loop, and we already have graphify.

## Ranked interventions

Ordered by (measured value × confidence), not by effort.

| # | intervention | closes | evidence | risk |
|---|---|---|---|---|
| ~~1~~ | **Skill lifecycle + decay** — `active/stale/archived` off the usage counters we already record; never delete | ⑤ DECAY | 421 skills, 33 used, 0 retired | **SHIPPED** `e45f3d3a` |
| ~~1b~~ | **Reinforce, don't duplicate** — the synthesizer deduped on evidence (trace_ids), so a lesson re-derived from a new incident always looked new | ④ FEEDBACK | 265 of 407 skills are numbered duplicates; one exists 21x | **SHIPPED** `48dbffd4` |
| ~~2~~ | **Structural death detection** — exception types instead of 19 English substrings | ① SIGNAL | governs every tool's retry path | **SHIPPED** `e6d09c1e` |
| ~~3~~ | ~~Adaptive breaker probe~~ — **WITHDRAWN.** Already implemented by `FX-02`; the probe count is explained to within 2.7% by the existing 900s backoff cap. Nothing to fix. | — | — | — |
| ~~5~~ | **Defer diagnosis during a substrate outage** — a loop must not depend on what it heals | ⑥ | 871 of 1,294 turns were futile RCA | **SHIPPED** `ae1f2702` |
| ~~6~~ | **Autonomic health in the brief** — the loops report themselves; live `skills_ever_used:28/411` | visibility | every finding here needed 2am jq | **SHIPPED** `048e74f8` |
| ~~7~~ | **RCA + JSON parser tolerance** — 409 verdicts discarded to markdown strictness | ③ | 409 of 410 had BOTH fields missing | **SHIPPED** `da4b859d`, `95c485ee` |
| ~~4~~ | **Make lesson injection falsifiable** — hold out a fifth of SESSIONS, label each outcome, compare quality per lane | ③ VERIFY | 2,193 turns injected, effect never observed | **SHIPPED** `f405b25b` |
| **5** | **Post-turn review fork** — the reference platform's loop, on our verification primitive | ②③④ | no equivalent exists | high — new subsystem |

Intervention **1** was first on merit, not convenience: the only one whose signal
was already recorded, whose action is deterministic and reversible, and whose
effect is immediately measurable in prompt size and tool-search ranking.

**1b was not on the original list.** It surfaced from the dry run: the first
names the curator proposed marking stale were `avoid_shell_for_web_fetching-1`
and `cronjob_fail_recovery_and_routing_fix-1/-2/-3`. Measuring that turned up
265 numbered duplicates of 142 base names — the improvement loop failing in
exactly the way the ADR describes, inside itself. Building the decay leg is what
made the duplication visible; that is the contract paying for itself immediately.

**Remaining: 5.** Everything else is shipped.

Intervention 4's design turned on one measurement made *before* building it: the
scored population is 3,702 machine-lane turns against 329 interactive, at 0.52 vs
0.39. A single aggregate comparison would have been 92% background jobs, hiding
the interactive answer entirely, and a lane-mix difference between arms could
have flipped its sign. It reports per lane instead. The arm is keyed on the
SESSION rather than the turn, because the lessons block is part of the system
prompt and a per-turn flip would have reintroduced the D01.1 defect while
measuring a different one.

### A methodological warning, earned twice in one session

Two of the four loops were characterised wrongly in the first draft, and both
times the same way: **I inferred a mechanism from outcome counts instead of
reading the mechanism.**

* Loop 1 — concluded "no adaptive backoff" from a 0.57% success rate. `FX-02` had
  built it; the arithmetic matched the design to within 2.7%.
* Loop 3 — concluded "lessons are never retrieved" from a count of 31. Those
  lines are DEBUG in an INFO deployment; the true figure is every turn.

**A count drawn from DEBUG lines in an INFO-level deployment measures the log
level, not the system.** D02.6 recorded exactly this trap for its `cause` field
one day earlier, and it still caught me twice. Any future measurement under this
ADR must state the log level of every line it counts, and must read the call site
before drawing a conclusion about the mechanism.

It is worth naming plainly, because it is the *same* failure the platform makes:
asserting a conclusion the evidence does not actually support.

## Invariants for anything built under this ADR

> **I1.** No autonomic mechanism ships without a named actuator, or a test
> pinning that it deliberately has none.
>
> **I2.** Signals are structural. No English-substring matching, in any locale.
>
> **I3.** Nothing is ever auto-deleted. Archive is the terminal state, and it is
> recoverable.
>
> **I4.** A human veto (`pinned`) outranks every automatic transition.
>
> **I5.** Improvement is written into an artifact the agent already reads.
> A store requiring voluntary retrieval does not count as a closed loop.
>
> **I6.** Every recovery event logs at WARNING. A silent self-heal is
> indistinguishable from a system that never had the problem — which is exactly
> how 1,391 failed probes went unnoticed.
>
> **I7.** Law 1 holds: no improvement mechanism may destabilise the per-conversation
> prompt cache. Reflection runs on its own cache, out of band.

## Verification

```bash
# RAN 2026-08-05 — every figure in this document

# Loop 1 — breaker transitions (the POSITIVE case)
cat ~/.stackowl/logs/stackowl*.jsonl | jq -r 'select(type=="object" and ((.msg//"")
  |test("state transition")))|.msg' | sed 's/.*state transition //' | sort | uniq -c
# -> 1401 OPEN->HALF_OPEN / 1391 HALF_OPEN->OPEN / 26 CLOSED->OPEN / 8 HALF_OPEN->CLOSED
# and 15d x 86400s / 900s cap = 1440 predicted -> the backoff is working, not absent

# Loop 3 — lessons stored vs applied
cat ~/.stackowl/logs/stackowl*.jsonl | jq -r 'select(type=="object" and ((.msg//"")
  |test("lesson";"i")))|.msg[0:78]' | sort | uniq -c | sort -rn
# -> 2680 stored / 31 gathered / 1 note_applied_lesson

# Loop 4 — skill catalog health
sqlite> select count(*), sum(n_executions>0), sum(n_executions) from skills;
# -> 421 | 33 | 208
```

## Related

- ADR-6 (`HealableResource`) — this ADR keeps the protocol and fixes its signal.
- [`D02.6`](hermes-mapping/designs/D02.6.md) — the recovery ladder; where the
  actuator obligation (②) was learned the expensive way.
- `progress.yml` `DEBT-39` — resolved by building the actuator rather than
  deferring it, which is obligation ② applied retroactively.
