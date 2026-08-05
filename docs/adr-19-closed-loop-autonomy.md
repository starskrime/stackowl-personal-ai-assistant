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

**StackOwl detects superbly, acts mechanically, and learns nothing — every
autonomic loop in the platform is open.** The machinery is not missing; it is
three-quarters built everywhere and finished nowhere. The last quarter — *feeding
the outcome back so the next attempt differs* — is absent from all four loops.

This is why "self-healing" and "self-improving" are not two projects. They are the
same control loop, and StackOwl is missing the same leg of it in both.

## The measurement

Four autonomic loops exist today. All four were measured end to end.

### Loop 1 — provider health (the circuit breaker)

| stage | measured |
|---|---:|
| detections (short-circuit events) | **43,908** |
| threshold trips (`CLOSED → OPEN`) | 26 |
| recovery probes (`OPEN → HALF_OPEN`) | **1,401** |
| probe failures (`HALF_OPEN → OPEN`) | 1,391 |
| **probe successes (`HALF_OPEN → CLOSED`)** | **8** |

**A 0.57% recovery rate over 1,401 attempts.** The loop runs constantly and almost
never closes. The reason is structural, not bad luck: **the probe is identical
every time.** It carries no memory of the previous 1,390 failures, no adaptive
backoff, no record of *which* failure cause was seen, and no change of approach.
This is a retry loop wearing a recovery loop's clothes.

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

### Loop 3 — lesson → application

| stage | measured |
|---|---:|
| lessons stored (`lessons_lance.publish`) | **2,680** |
| lesson retrievals (`classify._gather_lessons`) | 31 |
| lessons surfaced into a turn | 93 |
| **`note_applied_lesson` calls (15 days)** | **1** |

**2,680 lessons written, 1 applied.** And the mechanism explains it: application
requires the *model* to voluntarily call `note_applied_lesson`. The loop's final
leg is delegated to the discretion of the thing being corrected.

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

Every one of the four loops implements SENSE → DECIDE → ACT. None implements
VERIFY → LEARN → FEED BACK. So the root concept is not a new subsystem — it is a
**contract that every autonomic mechanism in StackOwl must satisfy**, and a
refusal to call anything "self-healing" until it does.

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
`infra/resilience.py::DEFAULT_DEAD_HANDLE_MARKERS` is a hardcoded list of 19
English substrings (`"Connection closed"`, `"database is locked"`, `"Broken
pipe"`…) used to decide whether a resource died. It is the same design D02.6
explicitly refused to port from the reference platform, sitting in our own ADR-6
foundation, and it silently fails for any non-English locale or reworded SDK
message. **This is the highest-leverage correctness fix in the concept.**

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
The missing leg, everywhere. Concretely:
- the breaker probe must carry the failure cause and an adaptive interval;
- an RCA verdict must change the next turn, not wait for the model to volunteer;
- a lesson must be injected, not offered.

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
| **1** | **Skill lifecycle + decay** — drive `active/stale/archived` off the usage counters we already record; never delete | ⑤ DECAY | 421 skills, 33 used, 0 retired | low — deterministic, reversible |
| **2** | **Structural death detection** — replace `DEFAULT_DEAD_HANDLE_MARKERS` with exception-type/errno classification | ① SIGNAL | 19 hardcoded English strings in the ADR-6 root | low — same call sites |
| **3** | **Adaptive breaker probe** — carry the failure cause, widen the interval on repeated failure | ④ FEEDBACK | 1,401 probes → 8 successes | medium |
| **4** | **Lesson injection** — inject at assembly instead of waiting for `note_applied_lesson` | ④ FEEDBACK | 2,680 stored → 1 applied | medium — touches the prompt (Law 1) |
| **5** | **Post-turn review fork** — the reference platform's loop, on our verification primitive | ②③④ | no equivalent exists | high — new subsystem |

Intervention **1** is first on merit, not convenience: it is the only one whose
signal is already recorded, whose action is deterministic and reversible, and
whose effect is immediately measurable in prompt size and tool-search ranking.

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

# Loop 1 — breaker transitions
cat ~/.stackowl/logs/stackowl*.jsonl | jq -r 'select(type=="object" and ((.msg//"")
  |test("state transition")))|.msg' | sed 's/.*state transition //' | sort | uniq -c
# -> 1401 OPEN->HALF_OPEN / 1391 HALF_OPEN->OPEN / 26 CLOSED->OPEN / 8 HALF_OPEN->CLOSED

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
