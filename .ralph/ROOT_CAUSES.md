# Root Causes

Causal ledger for the 7 themes in `RESEARCH_PLAN.md`. Each entry follows the
Root-Cause Method: symptom set → causal chain → generative power → evidence →
blast radius → prior art & gap. ADRs live in `.ralph/adr/`.

---

## T1 — Asserted-not-measured success (no single AcceptanceAuthority)

**Symptom set.** Findings where a result is *reported* without *observing* the intended effect:
F-29/30 (send_* report success on failed delivery), F-31 (shell `returncode==0`), F-32 (web_fetch
ignores HTTP status), F-82/83 (MCP empty-as-success), F-20/23 (provider empty-as-success), F-33/34
(write/media no read-back), F-25 (self-stamped `verified=True` trusted), F-80/81 (CLI claims without
confirm), F-11/12/13/14/15 (normal-turn acceptance off; only "saved file" observable; judge fails
open). Shared property: **the actor's word is the success signal.**

**Causal chain (5-whys).** Overclaim ships → `ToolResult.success=True` → success is set by the tool
itself (`returncode==0`, "backend returned non-str", "no exception") → there is no *post-condition*
the tool must be checked against → success is **asserted by the actor, never measured against the
declared effect**. The root is the last clause.

**Generative power.** One missing abstraction — *a declared, observable post-condition per effectful
action, checked by an authority distinct from the actor* — makes every overclaim writable. Worse,
"did it work?" is then re-decided independently by ≥6 proxies (per-tool `verified`, `AcceptanceChecker`,
`judge_delivery`, `giveup_floor`, `overclaim_gate`, the progress/`side_effect_committed` ledger), each
with its own gaps, because none of them is the *authority*.

**Evidence.** `tools/verification.py:62` `is_trustworthy_success(success, verified)` exists but only
some tools set `verified`; `pipeline/acceptance.py:208` `check()` is invoked only by
`objectives/driver.py` (normal turns pass `expected_outcome=None`); `pipeline/persistence.py:409`
`judge_delivery` fails open; `giveup_floor.py:157` and `overclaim_gate.py` each re-derive a verdict.
≥3 findings: F-29, F-31, F-15.

**Blast radius (latent).** Every *future* effectful tool (email send, calendar create, API POST,
DB write) will self-assert success until an authority forces a post-condition. The honesty floor and
learning loop both consume this signal, so the corruption propagates downstream (see T5).

**Prior art & gap.** B1–B4 introduced `ToolResult.verified` + `verify_artifact` + `is_trustworthy_success`
and a goal-level `AcceptanceChecker`. Gap: verification is *opt-in per tool* and *file-only*, and the
acceptance checker is wired only into the objectives driver. The authority exists in embryo; it is
neither mandatory, nor general over effect kinds, nor the single decider the proxies delegate to.

---

## T2 — Scattered give-up (no single RecoveryActuator ladder)

**Symptom set.** F-16/17/18 (provider faults dead-end, no tier fallback), F-5/6/7/8 (substitution/retry
gaps), F-24 (tool seam no retry), F-21 (narrow except), F-40/41 (objective blocked, no replan),
F-35/37/67 (turn/goal lost on crash/wedge), F-60/62 (job terminal-failed), F-64/65/66 (channel
transport dropped), F-55 (owl evolution). Shared property: **a recoverable failure is surrendered after
one attempt because recovery is a local concern of each call site.**

**Causal chain.** Failure surfaces to user → the call site caught it and returned → that call site has
no retry/fallback policy → because recovery is implemented ad-hoc per site (execute loop, gateway,
registry, scheduler, channel adapter, objective driver each invent their own) → there is **no single
recovery authority that classifies a failure and runs a bounded ladder** (retry → reroute → substitute
→ replan → honest-surrender).

**Generative power.** Without one ladder, every new failure path must *remember* to recover, and most
don't. The B2/B4 ladder (`execute.py`) only covers tool dispatch; provider, channel, objective, and
lifecycle failures each re-surrender.

**Evidence.** `pipeline/steps/execute.py` recovery ladder (B4) is tool-dispatch-only; `providers/
llm_gateway.py` cascades only on the ESCALATE *success signal* (F-16); `scheduler/scheduler.py`
`_mark_failed` is terminal for recurring jobs pre-S1 (F-60); channel adapters swallow transport
(F-64). ≥3 findings: F-16, F-40, F-64.

**Blast radius.** Any new effectful subsystem added later will re-surrender by default. Recovery
quality is proportional to how much the author remembered.

**Prior art & gap.** B4 recovery actuator + `_BRIDGING_RECOVERY_KINDS` + `TurnProgressTracker` circuit
breaker + `is_trustworthy_success`. Gap: it is bound to the tool-dispatch loop, not a reusable
authority that any failing operation (provider call, delivery, objective step) can hand a failure to.

---

## T3 — Ask-first reflex (no ReversibilityResolver)

**Symptom set.** F-3 (clarify surfaced without trying a default), F-27 (consent fails closed, no
reversibility tier), F-44 (objective hard-blocks on trivial clarify), F-68/69 (clarify parks),
F-70 (cost pause asks), F-71 (no auto-answer), F-56 (a2a default-deny). Shared property: **a reversible,
low-stakes decision is escalated to the human because there is no signal for "is this reversible?"**

**Causal chain.** Turn parks on a question → the clarify/consent path always parks → because there is
no per-action *reversibility/stakes* signal → "act-first on reversible, ask only on irreversible" lives
only as prose in prompts/descriptions, not as a code authority.

**Generative power.** Every gate (clarify tool, clarify_gateway, cost_pause, consent, router verdict)
re-decides "ask or act" with no shared signal, so each defaults to the safe-but-passive "ask."

**Evidence.** `interaction/consent.py:203` default tier `ALWAYS_ASK` with no reversibility notion;
`tools/interaction/clarify.py:200` unconditionally parks; `interaction/cost_pause.py` blocks on a soft
crossing. ≥3 findings: F-27, F-44, F-70.

**Blast radius.** Proactivity (the "Jarvis" goal) is structurally capped: the more capable the agent,
the more gates it hits, the more it asks.

**Prior art & gap.** `ConsequentialActionGate` + the `undo_write`/reversible-substitution class +
the dna_injector act-first directive. Gap: reversibility is computed nowhere as a first-class signal a
resolver can read; the act-first rule isn't enforced by code.

---

## T4 — Registered ≠ reachable (no Reachability invariant)

**Symptom set.** F-45 (heuristic store `find_for_tool` no caller), F-76 (/urgent never transports),
F-77 (digest job unseeded), F-78 (event bridge empty allow-list), F-86 (census built, never run).
Shared property: **a capability is wired as a dangling half-edge and ships green because reachability
is never asserted.**

**Causal chain.** Feature does nothing in prod → it was registered but never reached on the live path →
because registration and reachability are separate and only registration is checked → there is **no
invariant that every registered capability is provably reached** (the census that would prove it is
itself unreached — F-86, the bug eating its own tail).

**Generative power.** Tests assert "registered"; nothing asserts "reachable," so half-edges are
invisible until a human notices the feature is dead. This is the single most-repeated root in the
codebase's own history (per memory: "registered≠reachable").

**Evidence.** `health/reachability/census.py:62` `run_census` + `:85` `census_passes` exist but only
test callers; `StartupOrchestrator.run` never calls them. ≥3 findings: F-45, F-77, F-86.

**Blast radius.** Every future handler/tool/job/skill can ship dead. The pattern is structural, not
incidental.

**Prior art & gap.** The census + `census_passes` + the "Live Path Census fail-closed reachability law"
(memory). Gap: it's a library function, not a boot phase that fails loudly.

---

## T5 — Learning on an untrustworthy signal (positive-only)

**Symptom set.** F-45/46/47 (heuristics written, not read/consumed at decision time), F-26/43 (no
prior-outcome read before acting), F-48/51/54 (learners gate on `success=1 AND failure_class IS NULL`
— a *self-asserted* success), F-50 (recall recency-only), F-72 (classifier stateless). Shared property:
**the learning loop mines and reflects on a success signal that was never measured (T1), and the
positive-only directive forbids the obvious "learn from failure" fix.**

**Causal chain.** Agent repeats a known-bad approach → it never read prior outcomes before acting →
because learned signal is either unreachable (T4: F-45) or untrustworthy (T1: mines false wins) → and
the directive forbids storing negatives → there is **no architecture for trustworthy positive learning
+ within-turn failure awareness.**

**Generative power.** Until success is measured (T1), every mined "win" may be false; until learning is
reachable (T4), even true wins don't steer behavior. So the learning loop is a coping policy on a
broken signal (per memory's deepest-root diagnosis).

**Evidence.** `memory/outcome_store.py:176` `AND success = 1 AND failure_class IS NULL`;
`reflection_store.py` same predicate; B4b set `failure_class="unachieved_effect"` to keep false wins
out. ≥3 findings: F-45, F-48, F-51.

**Blast radius.** Every new learner (DNA, skills, heuristics, reflections) inherits the untrustworthy
signal and the reachability gap.

**Prior art & gap.** Positive-only learning (directive), B4b false-win exclusion, the heuristic/lessons
stores. Gap: mining isn't gated on *measured* success (depends on T1); the within-turn "avoid repeating
this failure *this turn*" awareness (allowed by the directive, since not persisted) doesn't exist.

---

## T6 — Detect-only lifecycle (no closed heal loop)

**Symptom set.** F-85 (watchdog pings on a blind timer), F-86/87 (census/health detect-only),
F-73/74 (supervisor restarts blindly, no goal nudge), F-36 (respawn no reconnect verify), F-39 (crash
no user notice), F-88 (mono no supervision). Shared property: **the system observes health/liveness but
never acts on a degraded signal and re-verifies.**

**Causal chain.** A wedged/degraded subsystem stays dead → something detected it but only logged/exited
→ because detection and remediation are separate and only detection is wired → there is **no closed
detect→heal→verify loop** that consumes a health signal, acts (recycle/restart/escalate), and confirms.

**Generative power.** Health is a read-only dashboard; nothing turns "down" into "healed." Every
lifecycle subsystem re-implements partial detection and no remediation.

**Evidence.** `service/watchdog.py` pings unconditionally; `health/aggregator.py` only run by the
`stackowl health` CLI; `supervisor/supervisor.py` restarts without progress. ≥3 findings: F-85, F-73, F-87.

**Blast radius.** Production outages self-perpetuate (the provider-box-down incident this session was
survived only by a human noticing).

**Prior art & gap.** `HealthAggregator`, `ResilienceContributor`, `attempt_with_recycle`, the census,
watchdog. Gap: all the pieces exist *unconnected*; nothing closes the loop. This is T4 (reachability)
applied to the lifecycle layer, plus a heal step that depends on T2.

---

## T7 — No decision trace (no DecisionLedger)

**Symptom set.** F-9/10 (recovery trace English-only / suppressed on floor), F-19 (escalation
success-only trace), F-28 (no next-step signal), F-39 (silent crash), F-47 (heuristic influence
opaque), F-72 (classifier verdict unlogged). Shared property: **why the agent did what it did is not
reconstructable because each decider traces ad-hoc or not at all.**

**Causal chain.** Can't explain a turn → the decision points logged inconsistently or not at all →
because there is no shared "decision" record type → each decider (router, recovery, acceptance,
heuristic, classifier) invents its own (or no) trace.

**Generative power.** Explainability is unachievable as long as it's voluntary and per-site. But: most
of these decisions are *exactly* the verdicts the T1–T6 authorities produce — so this theme is largely
*downstream*: give each authority a typed verdict and one ledger to emit to, and the trace falls out.

**Evidence.** `recovery_summary.py` hardcodes `_LANG="en"` (F-9); `llm_gateway.py:104` logs only the
ESCALATE branch (F-19). ≥3 findings: F-9, F-19, F-28.

**Blast radius.** Every new decider adds another blind spot.

**Prior art & gap.** The 4-point logging convention + `traceId`/`withSpan` observability. Gap: spans
capture *execution*, not *decisions+verdicts*; there's no queryable per-turn decision ledger.
