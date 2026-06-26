# Findings

Jarvis-vs-chatbot diagnostic audit. Evidence-backed; one entry per finding.
Each finding: file:line + excerpt + why-chatbot-like + fix-direction + severity.

Context: the codebase recently shipped a verification/recovery arc (B1–B4:
`ToolResult.verified`, hardened `verify_artifact`, recovery ladder, goal-level
`AcceptanceChecker`). Many smells are partly mitigated — noted where so.

---

## Module: Pipeline — ReAct execution core

### F-1: Goal-level acceptance verification OFF by default on normal turns
- Module: Pipeline — ReAct execution core
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/pipeline/acceptance.py:19-21; state.py:182-184; objectives/driver.py:262
- Evidence:
  ```python
  # acceptance.py — "Engaged only behind settings.acceptance_tier (default OFF)."
  # state.py — expected_outcome is None on a normal user turn ⇒ AcceptanceChecker no-ops
  tier = self._settings.acceptance_tier if self._settings is not None else ""
  ```
- Why it's chatbot-like: On an ordinary user turn the only post-action check is each tool's own per-artifact `verified` bit; the goal-level "did this accomplish what the user asked" authority is never invoked (objectives-path only, default-off), so a wrong-but-verified turn still reads as success.
- Fix direction: Allow AcceptanceChecker / an `expected_outcome` to be derived and checked on the normal execute path for consequential turns, not just the objectives driver.
- Severity: S3

### F-2: Normal pipeline is single-pass with no decompose/plan step
- Module: Pipeline — ReAct execution core
- Smell: Single-pass, no planning
- Pillar: goal persistence
- Location: src/stackowl/pipeline/registry.py:21-29
- Evidence:
  ```python
  PIPELINE_STEPS = [
      ("triage", triage.run), ("dispatch", dispatch.run), ("classify", classify.run),
      ("assemble", assemble.run), ("execute", execute.run),
      ("parliament_step", parliament_step.run), ("consolidate", consolidate.run),
  ]
  ```
- Why it's chatbot-like: The step sequence has no plan/decompose stage; a multi-step request is handed to a single `execute` ReAct loop and the planner is only reachable via the separate objectives subsystem, so a complex goal is improvised in one loop with no persisted plan.
- Fix direction: Insert an optional plan/decompose step (reusing planner.py) ahead of execute for work-classified turns, persisting the plan into state.
- Severity: S3

### F-3: Clarify verdict dropped in non-interactive contexts with no autonomous default
- Module: Pipeline — ReAct execution core
- Smell: Defers trivial decisions upward
- Pillar: proactivity
- Location: src/stackowl/pipeline/steps/execute.py:1772-1806
- Evidence:
  ```python
  if state.intent_class != "clarify" or not state.clarify_question:
      return None
  if not state.interactive:
      ...  # falls through to standard tool path
      return None
  await gateway.ask(state.session_id, state.channel, state.clarify_question, blocking=False, deliver=False)
  ```
- Why it's chatbot-like: When the router classifies a turn `clarify`, an interactive turn surfaces the question and yields to the user rather than resolving ambiguity with a sensible default first; the model's clarify verdict is taken at face value.
- Fix direction: Before surfacing, attempt to resolve from context/memory/defaults; only ask the human on a genuine, consequential ambiguity.
- Severity: S2

### F-4: Prior-failure outcomes are not read in the execute loop before acting
- Module: Pipeline — ReAct execution core
- Smell: No learning from prior failures
- Pillar: learning loop
- Location: src/stackowl/pipeline/steps/execute.py (no lessons/heuristic read in loop); assemble.py:170
- Evidence:
  ```python
  # assemble.py:170 — lessons/memory only folded as static prose into the prompt
  parts = [p for p in (base, persona, skills_block, state.memory_context) if p]
  # execute loop never consults lessons_index / heuristic_store at decision time
  ```
- Why it's chatbot-like: The execute loop never consults the lessons/heuristic store at decision time; prior-failure knowledge reaches the model only as frozen prompt text, so within-turn tool choices cannot be steered away from approaches that previously failed.
- Fix direction: Query the lessons/heuristic store inside `_dispatch` keyed on tool+goal before executing, biasing away from known-bad approaches.
- Severity: S3

---

## Module: Pipeline — recovery & containment

### F-5: Substitution actuator absorbs all exceptions and surrenders without alternate strategy
- Module: Pipeline — recovery & containment
- Smell: Gives up on tool failure
- Pillar: self-healing
- Location: src/stackowl/pipeline/steps/execute.py:464-470
- Evidence:
  ```python
  except Exception as exc:  # noqa: BLE001 — the actuator must never crash the turn
      log.engine.error("[pipeline] execute: self-heal substitution actuator failed — falling through", ...)
      return None
  ```
- Why it's chatbot-like: Any fault during route-around collapses to `return None` → honest surrender, with no second sibling tried and no distinction between "no sibling exists" and "the actuator broke." Self-heal is capped at one sibling attempt.
- Fix direction: On actuator exception, retry against the next-ranked candidate sibling before surrendering.
- Severity: S3

### F-6: Substitution is capped at one sibling per capability class per turn
- Module: Pipeline — recovery & containment
- Smell: Single-pass, no planning
- Pillar: goal persistence
- Location: src/stackowl/pipeline/steps/execute.py:429-449
- Evidence:
  ```python
  if not is_trustworthy_success(sib_result.success, sib_result.verified):
      ...  return None
  tag = sib.manifest.capability_tag
  if tag: substituted_tags.add(tag)
  ```
- Why it's chatbot-like: When the chosen sibling fails, the tag is excluded and a second eligible sibling in the same class is never tried even though `find_substitute` ranked multiple. The ladder tries one alternate then surrenders short of exhausting alternatives.
- Fix direction: Loop over ranked candidates (mark only the tried sibling, not the whole tag, as exhausted) until one yields trustworthy success.
- Severity: S2

### F-7: Retry-once restricted to unverified effects; genuine failures never get a retry rung
- Module: Pipeline — recovery & containment
- Smell: No learning from prior failures
- Pillar: self-healing
- Location: src/stackowl/pipeline/steps/execute.py:1130-1149
- Evidence:
  ```python
  if (tr.success and tr.verified is False and not is_consequential and name not in retried_unverified):
      retried_unverified.add(name)
      retry_tr = await _guarded_dispatch(args)
  ```
- Why it's chatbot-like: Rung 1 only fires for `success=True, verified=False`. A transient genuine failure (`success=False` from a timeout/network blip) gets zero retries and drops to substitution/surrender — a transient a single re-dispatch would clear is treated as terminal.
- Fix direction: Allow a bounded retry-once for classifiably-transient genuine failures (timeout/connection) before substitution.
- Severity: S2

### F-8: Apology cascade failure surrenders to a static neutral marker with no retry across tiers
- Module: Pipeline — recovery & containment
- Smell: Gives up on tool failure
- Pillar: self-healing
- Location: src/stackowl/pipeline/critical_failure.py:193-208
- Evidence:
  ```python
  try:
      result = await provider.complete([...], model="", max_tokens=_APOLOGY_MAX_TOKENS)
  except Exception as exc:  # outage mid-cascade
      ...  return None  # → non-localized neutral marker
  ```
- Why it's chatbot-like: One `provider.complete` is attempted from one tier; if it raises, it drops to an untranslated `⚠ [marker]` without advancing to the next provider/tier in the cascade.
- Fix direction: On `complete` failure, advance to the next provider/tier before falling to the neutral marker.
- Severity: S3

### F-9: Recovery-summary explanation is hardwired to English
- Module: Pipeline — recovery & containment
- Smell: Opaque / no trace
- Pillar: explainability
- Location: src/stackowl/pipeline/recovery_summary.py:18-23, 51-55
- Evidence:
  ```python
  _LANG = "en"  # turn language plumbing is out of scope; localize falls back to en
  text = localize_format(key, _LANG, failed=e.failed, recovered_via=e.recovered_via)
  ```
- Why it's chatbot-like: The user-visible recovery trace is always English regardless of `state.language` (while the giveup floor correctly localizes), so a non-English user gets an explanation they may not read.
- Fix direction: Thread `state.language` into `surface_recovery` instead of hardcoded `_LANG="en"`.
- Severity: S3

### F-10: Recovery annotation suppressed whenever the answer is a floor
- Module: Pipeline — recovery & containment
- Smell: No proactive initiative
- Pillar: explainability
- Location: src/stackowl/pipeline/recovery_summary.py:31-40
- Evidence:
  ```python
  has_real_answer = any(c.content.strip() and not c.is_floor for c in state.responses)
  if not has_real_answer:
      ...  return state  # all recovery events dropped
  ```
- Why it's chatbot-like: If the final response is an honest floor, all user-visible recovery events are dropped — so a floored user is never told the agent tried a substitution/fallback first, exactly when "I tried alternatives before giving up" would most distinguish a persistent agent.
- Fix direction: When floored, still surface a brief "attempted recovery via X" line (or fold into floor text).
- Severity: S3

---

## Module: Pipeline — acceptance & verification

### F-11: Normal user turns never verify acceptance — checker wired into objectives driver only
- Module: Pipeline — acceptance & verification
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/pipeline/state.py:181-184; objectives/driver.py:187-195
- Evidence:
  ```python
  expected_outcome: ExpectedOutcome | None = None        # state.py
  verdict = self._acceptance.check(criteria, turn_started_at=started_at,
      acted=bool(final_state.responses or final_state.tool_calls))  # driver.py — only caller
  ```
- Why it's chatbot-like: For the common chat path `expected_outcome` is always None and AcceptanceChecker no-ops; only multi-step "objectives" get post-condition verification. Everyday "I saved it / I did it" replies are accepted on self-assertion.
- Fix direction: Invoke AcceptanceChecker (or its LLM-derived layer) in the normal-turn backends post-deliver for consequential turns.
- Severity: S2

### F-12: Acceptance verification covers only "saved file" artifacts — every other effect unverifiable
- Module: Pipeline — acceptance & verification
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/pipeline/acceptance.py:132-147; acceptance_llm.py:88-91
- Evidence:
  ```python
  if outcome.kind == "artifact":
      observed = _dir_has_fresh_file(_resolve_dir(outcome.artifact_dir), turn_started_at)
  return AcceptanceVerdict(None, f"unknown outcome kind: {outcome.kind}")
  ```
- Why it's chatbot-like: The only measurable post-condition is "a fresh non-empty file appeared." "email sent," "message posted," "event created," "API called" map to no check and return `accepted=None`, so self-assertion stands for most consequential actions.
- Fix direction: Extend `ExpectedOutcome.kind` + checker with observable post-conditions for non-file effects (re-query API, check HTTP side-effect).
- Severity: S2

### F-13: Deterministic acceptance fails OPEN to "no opinion" on any filesystem error
- Module: Pipeline — acceptance & verification
- Smell: No verification after action (fail-open)
- Pillar: tool execution
- Location: src/stackowl/pipeline/acceptance.py:106-107, 133-139
- Evidence:
  ```python
  except OSError:
      return None   # _dir_has_fresh_file
  if observed is None:
      return AcceptanceVerdict(None, "artifact directory could not be observed")
  ```
- Why it's chatbot-like: When reality can't be observed (permission/transient FS fault) the verdict is `None` and the driver falls back to the agent's own "no errors" success — an inability to confirm becomes an implicit pass for a declared consequential outcome.
- Fix direction: For a declared consequential outcome, treat `accepted=None` (unobservable) as a soft-fail / retry-or-ask, not a fallback to self-asserted success.
- Severity: S3

### F-14: LLM-derived acceptance is flag-OFF by default — judged layer ships disabled
- Module: Pipeline — acceptance & verification
- Smell: No verification after action (disabled)
- Pillar: tool execution
- Location: src/stackowl/config/settings.py:790; objectives/driver.py:262-267
- Evidence:
  ```python
  acceptance_tier: str = ""          # settings.py default empty
  tier = self._settings.acceptance_tier if self._settings is not None else ""
  if not tier: return None           # driver._derive_acceptance → no LLM layer
  ```
- Why it's chatbot-like: The post-hoc layer that catches "draft claims it saved a file but didn't declare an outcome" is off unless explicitly configured; out of the box only an explicitly declared artifact criterion is ever verified, even within objectives.
- Fix direction: Ship a safe default tier, or surface in the trace that derived acceptance was skipped.
- Severity: S3

### F-15: Delivery judge (main-pipeline verification surrogate) fails OPEN — judge error counts as delivered
- Module: Pipeline — acceptance & verification
- Smell: No verification after action (fail-open)
- Pillar: tool execution
- Location: src/stackowl/pipeline/persistence.py:398-421
- Evidence:
  ```python
  except Exception as exc:  # fail OPEN — never block the turn on a judge error
      return True, JUDGE_ERROR_REASON
  if obj is None:
      return True, JUDGE_ERROR_REASON   # unparseable ⇒ delivered=True
  ```
- Why it's chatbot-like: On the path with no filesystem post-condition, `judge_delivery` is the only "did we deliver?" check, and any provider error or unparseable verdict resolves to `delivered=True` — a flaky/unreachable judge rubber-stamps every give-up as a delivery.
- Fix direction: On judge-error, retry on a fallback tier and, for consequential turns, fail toward "not delivered / continue" rather than accept.
- Severity: S2

---

## Module: Providers — LLM routing & gateway

### F-16: Provider call in escalation loop has no try/except — a raised fault dead-ends to the user
- Module: Providers — LLM routing & gateway
- Smell: Gives up on tool/API/LLM failure
- Pillar: self-healing
- Location: src/stackowl/providers/llm_gateway.py:97-114 (and 159-198)
- Evidence:
  ```python
  for idx, tier in enumerate(tiers):
      provider, _degraded = self._registry.resolve_tier_with_fallback(tier)
      result = await provider.complete(msgs, model="", **kwargs)   # no try/except
      if can_escalate and is_escalate_signal(result.content): continue
      return result
  ```
- Why it's chatbot-like: The cascade advances ONLY on the in-band `ESCALATE` success-text signal. A `ProviderError`/`CircuitOpenError`/`RateLimitError`/timeout at the chosen tier propagates out of the loop — the gateway never falls back to the next tier for a *failed* call. Cascade is complexity-based, not failure-based.
- Fix direction: Wrap each per-tier call; on a classified provider fault, `continue` to the next tier (emit degraded trace) instead of re-raising.
- Severity: S1

### F-17: Failure-based fallback only fires on a PRE-CALL OPEN breaker, not on the actual call failure
- Module: Providers — LLM routing & gateway
- Smell: Gives up on tool/API/LLM failure
- Pillar: self-healing
- Location: src/stackowl/providers/registry.py:534-545
- Evidence:
  ```python
  breaker = breakers.get(primary_name)
  if breaker is None or breaker.state is not CircuitState.OPEN:
      return providers[primary_name], None
  healthy = self.get_with_cascade(tier)
  return healthy, primary_name
  ```
- Why it's chatbot-like: `resolve_tier_with_fallback` cascades only if the circuit is ALREADY OPEN. The first request that trips a provider still routes to it; the call raises and (per F-16) reaches the user. The breaker helps *next time* — the user must re-ask to benefit.
- Fix direction: Pair the pre-call OPEN check with a post-failure in-loop fallback: on a classified fault, re-resolve via `get_with_cascade` and retry the round once on the next healthy provider.
- Severity: S1

### F-18: Rate-limiter cap refusal propagates with no alternate-provider fallback
- Module: Providers — LLM routing & gateway
- Smell: Gives up on API failure
- Pillar: self-healing
- Location: src/stackowl/providers/rate_limiter.py:153-168; consumed at _resilient_round.py:219-224
- Evidence:
  ```python
  if self._refill_rate <= 0.0:
      raise RateLimitError(self._provider_name, tokens, self._capacity)   # rate_limiter
  except RateLimitError:
      raise                                                               # _resilient_round
  ```
- Why it's chatbot-like: `RateLimitError` is fail-closed for that provider, but nothing re-routes to a non-capped provider/tier, so a rate-limited fast tier surfaces an error rather than transparently shifting to standard/powerful.
- Fix direction: Treat `RateLimitError` like an OPEN breaker in the gateway loop — skip the capped provider and cascade.
- Severity: S2

### F-19: Routing is traced, but the escalation cascade is success-driven only — no failure trace on give-up
- Module: Providers — LLM routing & gateway
- Smell: No proactive initiative on failure
- Pillar: explainability
- Location: src/stackowl/providers/llm_gateway.py:104-114
- Evidence:
  ```python
  if can_escalate and is_escalate_signal(result.content):
      log.engine.info("[llm_gateway] complete: model escalated — stepping up tier", ...)
      continue
  return result
  ```
- Why it's chatbot-like: Every logged decision is a *model-requested* escalation; there is no branch/log for "tier N failed, falling back" because failures are never caught here — the trace can never explain a provider-error outcome.
- Fix direction: Add explicit failure-branch logging (`from_tier`, `exc_type`, `degraded_from`) once the calls are wrapped.
- Severity: S3

---

## Module: Providers — backend adapters

### F-20: Anthropic complete() accepts empty generation as success — no verification, no retry
- Module: Providers — backend adapters
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/providers/anthropic_provider.py:644-653
- Evidence:
  ```python
  content = "".join(b.text for b in response.content if hasattr(b, "text"))
  result = CompletionResult(content=content, ...)   # no emptiness check
  ```
- Why it's chatbot-like: An empty/whitespace `content` is wrapped as success — unlike OpenAI's sibling which retries once — so the caller cannot tell a real answer from silence.
- Fix direction: If `content` is empty after extraction, retry the round once (or floor honestly) before returning.
- Severity: S3

### F-21: Anthropic stream/complete catch only `anthropic.APIError`, letting other faults escape unwrapped
- Module: Providers — backend adapters
- Smell: Gives up on API failure
- Pillar: self-healing
- Location: src/stackowl/providers/anthropic_provider.py:124-130 (and 637-643)
- Evidence:
  ```python
  except anthropic.APIError as exc:
      log.engine.error("[anthropic] stream: API error", exc_info=exc, ...)
      raise ProviderError(self._name, exc) from exc
  ```
- Why it's chatbot-like: Only `anthropic.APIError` is caught; a raw `ConnectionError`/`TimeoutError` escapes as a non-`ProviderError`, bypassing the uniform failure contract (narrower than the stdlib faults `_is_transport_error` knows).
- Fix direction: Catch a broader transport set (or `Exception`) and wrap as `ProviderError` consistently, as Gemini already does.
- Severity: S3

### F-22: OpenAI empty-generation retry re-issues the identical call — no variation, no learning
- Module: Providers — backend adapters
- Smell: No learning from prior failures
- Pillar: learning loop
- Location: src/stackowl/providers/openai_provider.py:814-831
- Evidence:
  ```python
  if not content:
      log.engine.warning("[openai] complete: empty after think-strip — retrying once", ...)
      retry = await self._resilient_round(_round)   # byte-identical call
  ```
- Why it's chatbot-like: The retry replays the identical `_round` (same prompt/params/temp) so a deterministic empty generation repeats; no nudge/param change. If the second is also empty, an empty result is returned as success.
- Fix direction: On retry vary a parameter (max_tokens/temperature/nudge); if still empty, floor honestly rather than returning "".
- Severity: S4

### F-23: Gemini complete()/stream() return empty text as success — no emptiness check
- Module: Providers — backend adapters
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/providers/gemini_provider.py:193-203
- Evidence:
  ```python
  text = response.text or ""
  result = CompletionResult(content=text, ...)   # no finish_reason / prompt_feedback check
  ```
- Why it's chatbot-like: `response.text or ""` coerces a blocked/safety-filtered generation into an empty-but-"successful" result; the caller cannot distinguish a real answer from a suppressed one. Same in `stream`.
- Fix direction: Inspect `finish_reason`/`prompt_feedback`; on empty/blocked, retry once or floor honestly.
- Severity: S3

---

## Module: Tools — execution framework

### F-24: Tool.__call__ wraps failures with no retry, fallback, or capability-sibling substitution
- Module: Tools — execution framework
- Smell: Gives up on tool failure
- Pillar: self-healing
- Location: src/stackowl/tools/base.py:167-176
- Evidence:
  ```python
  try:
      result = await self.execute(**kwargs)
  except Exception as exc:
      log.tool.error(...)
      result = ToolResult(success=False, output="", error=str(exc), duration_ms=duration_ms)
  ```
- Why it's chatbot-like: The single execution seam catches, logs, returns a failed result — it never retries or routes to a `capability_tag` sibling, though the manifest field exists "so the supervisor can route to a sibling." The actuator is declared but not invoked here.
- Fix direction: On a failed/`verified=False` result, have the seam (or caller) consult `capability_tag` and attempt bounded substitution/retry before surfacing failure.
- Severity: S2

### F-25: Verification seam skipped when the tool self-stamps verified; trusts any tool-supplied verdict
- Module: Tools — execution framework
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/tools/base.py:181-197
- Evidence:
  ```python
  if result.success and result.verified is None:
      verdict = await self.verify(kwargs, result, started_at=started_at)
      if verdict is not None:
          result = result.model_copy(update={"verified": verdict})
  ```
- Why it's chatbot-like: The hardened `verify()` only runs when `verified is None`. Any tool returning `verified=True` itself bypasses the reality check — a self-asserted verification is trusted with no independent read-back, the exact failure mode B1 meant to close.
- Fix direction: Treat tool-supplied `verified=True` as a claim, not proof; still run `verify_artifact` (or require verdicts come only from the seam).
- Severity: S2

### F-26: Registry is a passive holder — dispatch never consults prior tool outcomes
- Module: Tools — execution framework
- Smell: No learning from prior failures
- Pillar: learning loop
- Location: src/stackowl/tools/registry.py:270-290
- Evidence:
  ```python
  def get(self, name: str) -> Tool | None:
      with self._lock: return self._tools.get(name)
  def all(self) -> list[Tool]:
      with self._lock: return list(self._tools.values())
  ```
- Why it's chatbot-like: The only lookup/dispatch surface returns the tool with zero reference to any `tool_outcome_ledger`/past-failure record, so the framework re-attempts a known-failing tool/arg pattern identically each time.
- Fix direction: Before dispatch, consult the outcome ledger for recent repeated failures of the same tool/arg shape and short-circuit or annotate.
- Severity: S3

### F-27: Consent gate fails CLOSED with no reversibility/triviality tier
- Module: Tools — execution framework
- Smell: Defers trivial decisions upward
- Pillar: proactivity
- Location: src/stackowl/tools/consent.py:203-219
- Evidence:
  ```python
  tier = self.tiers.get(tool_name, TrustTier.ALWAYS_ASK)
  ...
  if not excluded and tier is TrustTier.AUTO:
      return self._finalize(True, ...)
  ```
- Why it's chatbot-like: Default tier is `ALWAYS_ASK` with no reversibility notion; every consequential action — even one trivially reversible via `undo_write` — falls through to a user prompt unless an operator pre-configured AUTO, biasing toward asking rather than acting-and-undoing.
- Fix direction: Add a reversibility signal (tool declares `reversible`/undoable) so policy can auto-allow-with-undo for low-blast-radius reversible actions.
- Severity: S3

### F-28: No proactive initiative or planning surface in the execution framework
- Module: Tools — execution framework
- Smell: No proactive initiative
- Pillar: proactivity
- Location: src/stackowl/tools/base.py:156-205
- Evidence:
  ```python
  async def __call__(self, **kwargs: object) -> ToolResult:
      result = await self.execute(**kwargs)
      return result
  ```
- Why it's chatbot-like: The seam runs exactly one `execute()` and returns — no post-result hook to propose a next action, no plan-progress check, no self-initiated follow-up on `verified=False`.
- Fix direction: Emit a structured signal on `verified=False`/failure the supervisor can use to drive a next step.
- Severity: S4

---

## Module: Tools — tool implementations

### F-29: send_file reports success=True even when delivery failed or was deferred
- Module: Tools — tool implementations
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/tools/scheduling/send_file.py:226-238 (and `_ok` 364-378)
- Evidence:
  ```python
  status = await self._deliver(str(resolved), caption, target, trace_id, session_id)
  record = {..., "delivery_status": status}
  return self._ok(record, t0, note=f"send_file {status}", ...)
  # _ok ALWAYS: return ToolResult(success=True, ...)
  ```
- Why it's chatbot-like: `_deliver` returns `"failed"`/`"deferred"` yet `_ok` unconditionally sets `success=True`; the failure is buried in a JSON field the success flag never reflects, and with no `artifact_path` the B1 verify hook can't catch it.
- Fix direction: Map `delivery_status in {"failed","deferred"}` to `success=False`/`verified=False` so the agent sees the byte never reached the user.
- Severity: S1

### F-30: send_message reports success=True on a failed/deferred send
- Module: Tools — tool implementations
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/tools/scheduling/send_message.py:220-228 (and `_ok` 283-297)
- Evidence:
  ```python
  status = await self._deliver(text, target, trace_id, session_id)
  record = {"action": "send", ..., "delivery_status": status}
  return self._ok(record, t0, note=f"send {status}", ...)   # _ok → success=True
  ```
- Why it's chatbot-like: Same pattern as send_file — `_deliver` can return `"failed"`/`"deferred"` (even logs the failed case) but still self-asserts `success=True`. A proactive message that never reached the user is indistinguishable from one that did.
- Fix direction: Demote `success` when `delivery_status` is failed/deferred; surface deferral distinctly.
- Severity: S1

### F-31: shell reports success on a non-zero command without effect verification
- Module: Tools — tool implementations
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/tools/system/shell.py:485-500
- Evidence:
  ```python
  success = proc.returncode == 0
  output = stdout.decode("utf-8", errors="replace").strip()
  return ToolResult(success=success, output=output, error=error, duration_ms=duration_ms)
  ```
- Why it's chatbot-like: `success` is bound purely to `returncode == 0`; ShellTool has no `verify()` and emits no `artifact_path`, so for the most powerful state-changing tool the claimed effect is never read back — a command that exits 0 but produced nothing still reports success.
- Fix direction: Where the command names an output artifact, capture `artifact_path` + add `verify()`; treat empty-output writes as unverified.
- Severity: S2

### F-32: web_fetch self-asserts success without checking HTTP status
- Module: Tools — tool implementations
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/tools/io/web_fetch.py:144-190
- Evidence:
  ```python
  status, html = await with_browser_retry(_do, runtime, op_name="web_fetch")
  ...
  return ToolResult(success=True, output=output, duration_ms=duration_ms)   # status unused
  ```
- Why it's chatbot-like: `status` is captured/logged but never gates the result — a 404/500 error page (or `status==0`) is extracted to markdown, returned as `success=True`, and even auto-staged into memory as a fact.
- Fix direction: Treat non-2xx/`status==0` as `success=False` (and don't stage as a memory fact); surface HTTP status so the agent can retry.
- Severity: S2

### F-33: write_file does not read back content; verify only checks existence/freshness
- Module: Tools — tool implementations
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/tools/io/write_file.py:60-67, 87-98
- Evidence:
  ```python
  async def verify(self, args, result, *, started_at):
      return verify_artifact(result.artifact_path, not_before=started_at)
  await asyncio.to_thread(target.write_text, content, encoding="utf-8")
  return ToolResult(success=True, output=f"Written: {path_str}", ..., artifact_path=str(target))
  ```
- Why it's chatbot-like: `verify_artifact` only asserts the file exists/non-empty/fresh — never compares persisted bytes to `content`. A short/truncated write passes. (Contrast `edit.py:172-193` which read-back compares and auto-restores.)
- Fix direction: Read the file back and compare length/content (as `edit` does), or confirm on-disk size equals `len(content.encode())`.
- Severity: S3

### F-34: image_generate / tts trust the backend's success without reading bytes
- Module: Tools — tool implementations
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/tools/media/image_generate.py:152-161 (twin: media/tts.py:148-158)
- Evidence:
  ```python
  outcome = await backend.generate(args.prompt, size=args.size)
  if isinstance(outcome, str): return self._err(outcome, t0)
  return self._ok(outcome, t0)   # _ok → success=True from a non-str outcome alone
  ```
- Why it's chatbot-like: `_ok` infers success purely from "backend returned a non-str object," never inspecting bytes inline. (Mitigated: a real `verify()` magic-byte hook catches it downstream — but `execute` self-asserts before any observation.)
- Fix direction: Make `execute` defensive (check `result.path` exists/non-empty) so success isn't asserted before observation.
- Severity: S3

---

## Module: Runtime — gateway/core split

### F-35: In-flight turn lost on core crash — finalize ends the reader but never replays the turn
- Module: Runtime — gateway/core split
- Smell: Goal amnesia across turns
- Pillar: goal persistence
- Location: src/stackowl/runtime/gateway_link.py:138-141, 144-172
- Evidence:
  ```python
  async def submit(self, msg):
      if self._conn is None or self._buffering:
          self._pending.append(msg)   # only buffers messages NOT YET sent
          return
      await self._do_submit(msg)      # already-sent msg tracked nowhere
  ```
- Why it's chatbot-like: A turn already forwarded to the core (the common case) is held in no gateway structure. If the core crashes mid-turn, `finalize()` terminates the reader so the spinner stops, but the objective is forgotten — the user sees a dead turn and must re-ask. Only messages arriving *during* the gap are in `_pending`.
- Fix direction: Track submitted-but-unfinished turns in an in-flight set (keyed by trace_id); on drop/finalize re-queue those whose stream never closed into `_pending` for replay on the next Hello (idempotent via request_id).
- Severity: S1

### F-36: Crash respawn does not re-arm the boot-timeout guard
- Module: Runtime — gateway/core split
- Smell: No verification after action
- Pillar: self-healing
- Location: src/stackowl/startup/orchestrator.py:188-204
- Evidence:
  ```python
  rc = await proc.wait()
  await asyncio.sleep(backoff); backoff = min(backoff * 2, 30.0)
  with contextlib.suppress(Exception):
      proc_holder["proc"] = await spawn_core(socket_path)   # no reconnect verify
  ```
- Why it's chatbot-like: Respawn fires `spawn_core` and loops back to `proc.wait()` without waiting for/verifying the fresh core's Hello (unlike first boot's `_CORE_BOOT_TIMEOUT_S`). A respawned core that boots but never connects leaves the gateway buffering every new turn indefinitely with no timeout/error.
- Fix direction: After `spawn_core`, await reconnect with the same bounded timeout; on timeout, surface an operator-visible failure and stop silently buffering.
- Severity: S2

### F-37: Drain stragglers abandoned on restart with no replay or user notice
- Module: Runtime — gateway/core split
- Smell: Gives up on failure
- Pillar: self-healing
- Location: src/stackowl/runtime/drain.py:55-66
- Evidence:
  ```python
  while turn_registry.has_active_turns():
      if loop.time() >= deadline:
          log.gateway.warning("[runtime] quiesce: grace ceiling reached — restarting with stragglers still running", ...)
          return False
      await asyncio.sleep(poll_interval_s)
  ```
- Why it's chatbot-like: A turn past `grace_seconds` is logged "abandoned" and the core `execv`s anyway. The docstring asserts durable turns resume, but `quiesce` does nothing to ensure that and the gateway has no replay path for a mid-stream (non-checkpointed) turn — silently cut, user left hanging.
- Fix direction: Before returning False, emit a user-facing "interrupted by restart, retrying" via the sink, and gate the resumability claim on an actual checkpoint per straggler request_id.
- Severity: S2

### F-38: Buffered/flushed messages swallow exceptions with no retry or user feedback
- Module: Runtime — gateway/core split
- Smell: Gives up on failure
- Pillar: self-healing
- Location: src/stackowl/runtime/gateway_link.py:174-185
- Evidence:
  ```python
  for msg in pending:
      with contextlib.suppress(Exception):
          await self._do_submit(msg)
  ```
- Why it's chatbot-like: After reconnect each buffered turn is replayed once and any failure is silently suppressed — the user who waited through the restart gets nothing and no error. Same swallow-and-forget at line 190-192.
- Fix direction: On replay failure, re-queue (bounded retries) or notify the originating adapter, rather than `suppress(Exception)` and drop.
- Severity: S3

### F-39: Core dispatch crash closes the stream but never tells the user the turn failed
- Module: Runtime — gateway/core split
- Smell: Opaque / no trace
- Pillar: explainability
- Location: src/stackowl/runtime/core_link.py:108-123
- Evidence:
  ```python
  except Exception as exc:  # noqa: BLE001 — never let one turn kill the link
      log.gateway.error("[ipc] core link: dispatch failed", exc_info=exc, ...)
  finally:
      with contextlib.suppress(Exception):
          await sink.close_stream()   # empty/short answer, no error chunk
  ```
- Why it's chatbot-like: When a turn's dispatch raises, the only recovery is `close_stream()`; the user sees a truncated/empty answer with no indication the turn errored (logged server-side only).
- Fix direction: Before/within `close_stream`, emit a terminal error chunk / failure notice so the channel delivers a visible "the turn failed."
- Severity: S3

---

## Module: Objectives — goal persistence

### F-40: Failed/parked sub-goal blocks the objective with no retry, replan, or escalation
- Module: Objectives — goal persistence
- Smell: Gives up on failure
- Pillar: self-healing
- Location: src/stackowl/objectives/driver.py:169-175
- Evidence:
  ```python
  if final_state.errors:
      err = "; ".join(final_state.errors)
      await store.update_subgoal(nxt.subgoal_id, "failed", result=err, task_id=task_id)
      await store.update_status(objective.objective_id, "blocked", blocker=err)
      await self._notify(objective, f"⚠ Objective stalled: {objective.intent}\n{err}")
      return True
  ```
- Why it's chatbot-like: On the first sub-goal error the objective is marked `blocked` and the owner notified — no retry budget, no alternate plan, no re-decomposition. Since `_advance` only picks `next_pending_subgoal` and the driver scans `status="active"` only, a `blocked` objective is never picked up again — a single transient failure permanently strands the goal.
- Fix direction: Add bounded retry/backoff on the failed sub-goal and, on exhaustion, invoke the decomposer to replan the remainder before escalating to `blocked`.
- Severity: S1

### F-41: Active-only scan plus terminal `blocked` status means a stalled objective is never resumed
- Module: Objectives — goal persistence
- Smell: Gives up on failure
- Pillar: goal persistence
- Location: src/stackowl/objectives/driver.py:102-103, 110-113
- Evidence:
  ```python
  active = await store.list_objectives(status="active")
  for objective in active:
      if await self._advance(store, objective): ...
  ```
- Why it's chatbot-like: The driver only ever advances `active` objectives; every failure/park path transitions to `blocked` and nothing ever transitions `blocked` back to `active`. Once stalled, the goal is abandoned by the autonomous loop and can only be revived by user action.
- Fix direction: Make `blocked` recoverable — re-queue after a cooldown, distinguishing "blocked-on-irreversible-decision" (needs human) from "stalled-on-transient-error" (auto-retry).
- Severity: S2

### F-42: Sub-goal with no declared acceptance criterion completes on no-error self-assertion
- Module: Objectives — goal persistence
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/objectives/driver.py:184-211
- Evidence:
  ```python
  criteria = nxt.acceptance_criteria or await self._derive_acceptance(...)   # None when tier off
  verdict = self._acceptance.check(criteria, ...)   # no-ops when criteria is None
  await store.update_subgoal(nxt.subgoal_id, "done", result=response_text, task_id=task_id)
  ```
- Why it's chatbot-like: When no criterion was declared and the optional LLM deriver is off (the default), `criteria` is None and AcceptanceChecker no-ops; the sub-goal is marked `done` purely because no error was thrown. The decomposer only attaches a criterion on a `<<produces-file>>` marker, so the common fetch/read/summarize/notify step has no post-condition.
- Fix direction: Require every sub-goal to carry a checkable criterion (or make the LLM-derived layer the default), so `done` reflects an observed effect.
- Severity: S1

### F-43: Driver never reads prior objective/sub-goal outcomes before retrying or advancing
- Module: Objectives — goal persistence
- Smell: No learning from prior failures
- Pillar: learning loop
- Location: src/stackowl/objectives/driver.py:135-155
- Evidence:
  ```python
  async def _advance(self, store, objective) -> bool:
      nxt = await store.next_pending_subgoal(objective.objective_id)
      await store.update_subgoal(nxt.subgoal_id, "running")
      final_state, task_id = await self._run_subgoal(objective, nxt.description, nxt.acceptance_criteria)
  ```
- Why it's chatbot-like: `_advance` runs the next sub-goal cold — never consults `store.list_events` or prior `failed` results (the event log/`result` column exist but are write-only here), so the loop cannot adapt and would repeat a failing approach if retried.
- Fix direction: Before running a sub-goal, load prior failure events/results and feed them into the sub-goal context or a replan.
- Severity: S3

### F-44: Sub-goal runs non-interactively with clarifications routed to a hard block
- Module: Objectives — goal persistence
- Smell: Defers trivial decisions upward
- Pillar: proactivity
- Location: src/stackowl/objectives/driver.py:158-167, 230-237
- Evidence:
  ```python
  if final_state.durable_parked:
      blocker = "; ".join(final_state.errors) or "awaiting a decision"
      await store.update_status(objective.objective_id, "blocked", blocker=blocker)
      await self._notify(objective, f"⏸ Objective needs your decision: {objective.intent}\n{blocker}")
  ```
- Why it's chatbot-like: The pipeline runs `interactive=False`, so any clarify/consent need — including trivial ones — becomes `durable_parked`, escalates the whole objective to `blocked`, and pings the owner. No attempt to resolve reversible/low-stakes ambiguity autonomously.
- Fix direction: Classify parks by reversibility/stakes and auto-resolve trivial/reversible decisions with a default; reserve escalation for irreversible choices.
- Severity: S2

---

## Module: Learning — outcome mining & lessons

### F-45: SQLite heuristic store is write-only on the live path — find_for_tool has no caller
- Module: Learning — outcome mining & lessons
- Smell: No learning from prior failures (written, never read before acting)
- Pillar: learning loop
- Location: src/stackowl/learning/tool_heuristic_store.py:113-132; call-site execute.py:1106-1108
- Evidence:
  ```python
  # execute.py live dispatch path, AFTER each tool runs:
  match_and_log(tool_name=name, tool_result=r)   # no DB lookup
  # tool_heuristic_store.py — the only reader, with ZERO callers:
  async def find_for_tool(self, tool_name, *, min_evidence=3) -> list[ToolHeuristic]: ...
  ```
- Why it's chatbot-like: The miner faithfully writes `tool_heuristics` rows, but the only structured reader (`find_for_tool`) has zero callers and the live path calls `match_and_log` (no lookup). The structured heuristic memory is registered but unreachable — it never steers a tool call before it runs.
- Fix direction: Re-introduce a guarded pre-dispatch read (`find_for_tool` gated on high evidence/quality) so a mined "tool X under condition Y → fails" pattern can warn/re-route before the tool fires.
- Severity: S1

### F-46: ToolHeuristic.mean_quality is persisted but feeds no decision
- Module: Learning — outcome mining & lessons
- Smell: Opaque / no trace (signal stored, never consumed)
- Pillar: learning loop
- Location: src/stackowl/learning/heuristic_matcher.py:11-15
- Evidence:
  ```python
  # Note (F049): only ToolHeuristic.mean_quality (the heuristic-store row field) is
  # currently unread — persisted by the miner and rendered in __str__ but feeds no decision.
  ```
- Why it's chatbot-like: The miner computes/stores a confidence signal that nothing reads to weight a decision — confirming the heuristic-store row is a dead artifact, not an input to behavior.
- Fix direction: Either consume `mean_quality` in a pre-dispatch confidence gate, or drop it to avoid a misleading "we learn confidence" surface.
- Severity: S3

### F-47: Heuristics surface into the prompt with no provenance/evidence on the decision path
- Module: Learning — outcome mining & lessons
- Smell: Opaque / no trace
- Pillar: explainability
- Location: src/stackowl/pipeline/steps/classify.py:386-403
- Evidence:
  ```python
  hits = await lessons_index.search(query, limit=limit)
  non_skill_hits = [h for h in hits if h.source_type != "skill"]
  ranked = rank_lessons(non_skill_hits)
  ```
- Why it's chatbot-like: The only live consumption of mined heuristics is as opaque free-text lesson lines blended into a classify prompt by ANN similarity; the structured `evidence_count`/`failure_class`/`predicted_outcome` fields are flattened to a string with no trace tying a chosen action to a specific heuristic.
- Fix direction: Carry heuristic id + evidence_count through `rank_lessons` into the surfaced-lesson trace so a heuristic-influenced decision is auditable.
- Severity: S3

> NOT a finding (verified): the B4b false-win gate is correctly wired — asyncio_backend stamps `failure_class="unachieved_effect"` for unrecovered effectful failures and the miner (`if o.failure_class: continue`) skips them, so the positive-only miner does NOT mine `verified==False` self-asserted wins.

---

## Module: Memory — read/write & reflection

### F-48: Positive-only learning: failures are never remembered, so the agent can't avoid repeating them
- Module: Memory — read/write & reflection
- Smell: No learning from prior failures
- Pillar: learning loop
- Location: src/stackowl/memory/reflection_store.py:47-63
- Evidence:
  ```sql
  WHERE o.owner_id = ? AND r.reflection_id IS NULL
    AND o.quality_score IS NOT NULL AND o.failure_class IS NULL
    AND o.success = 1 AND o.quality_score >= 0.6
  ```
- Why it's chatbot-like: Reflections (read back at act time) are written ONLY for successes; `failure_class IS NULL AND success = 1` excludes every failure. The agent can never recall "last time I tried X it failed" — no negative learning loop. (Note: positive-only storage is an explicit operator directive; flagged here as the systemic behavioral consequence.)
- Fix direction: Also reflect on failures (a "what to avoid" lesson with `failure_class` set) and surface them at recall so the live path can steer away — or accept the directive and document the cost.
- Severity: S2

### F-49: Reflection recall on the live path fails open to empty — agent acts without its own lessons
- Module: Memory — read/write & reflection
- Smell: No learning from prior failures
- Pillar: learning loop
- Location: src/stackowl/pipeline/steps/classify.py:166-184
- Evidence:
  ```python
  try:
      store = ReflectionStore(db)
      reflections = await store.recent_for_owl(owl_name, limit=limit)
  except Exception as exc:  # B5
      log.engine.warning("[pipeline] classify._gather_recent_reflections: lookup failed — skipping", ...)
      return ""
  ```
- Why it's chatbot-like: This is the ONE live read of learned reflections before acting. On any DB/recall error it logs-and-returns `""` — the turn proceeds with zero learned context, no retry/heal, and no signal to the user. The agent silently reverts to memoryless exactly when memory is broken.
- Fix direction: Distinguish "no reflections" from "recall failed"; on error retry once and/or annotate the context that learned memory is degraded.
- Severity: S2

### F-50: Reflection retrieval is recency-only, not semantic — relevant past lessons not surfaced
- Module: Memory — read/write & reflection
- Smell: No proactive initiative
- Pillar: proactivity
- Location: src/stackowl/memory/reflection_store.py:225-240
- Evidence:
  ```python
  async def recent_for_owl(self, owl_name, limit=5):
      rows = await self._db.fetch_all(
          "...FROM reflections WHERE owner_id=? AND owl_name=? ORDER BY created_at DESC LIMIT ?", ...)
  ```
- Why it's chatbot-like: The live path uses last-N by `created_at`, so a highly relevant lesson from last week is invisible while three unrelated recent ones surface. The embedding column exists but isn't queried on the live path.
- Fix direction: Wire semantic recall (embedding/lessons_index ANN) into classify so reflections matching the current intent surface, with recency as tie-breaker.
- Severity: S3

### F-51: Outcome capture is positive-only — failed runs never scored or mined
- Module: Memory — read/write & reflection
- Smell: No learning from prior failures
- Pillar: learning loop
- Location: src/stackowl/memory/outcome_store.py:140-169
- Evidence:
  ```sql
  SELECT ... FROM task_outcomes
   WHERE owner_id = ? AND quality_score IS NULL
         AND success = 1 AND failure_class IS NULL
   ORDER BY captured_at ASC LIMIT ?
  ```
- Why it's chatbot-like: The critic (feeding reflection, DNA attribution, tool-mining) only scores `success=1 AND failure_class IS NULL`; every downstream learner consumes successes only, so the agent's behavioral model is blind to what doesn't work.
- Fix direction: Add a failure-analysis path (even a lightweight "avoid" classifier) so the learning loop is bidirectional.
- Severity: S3

---

## Module: Owls — DNA, evolution & routing

### F-52: DNA traits modulate tone/register only — no trait drives persistence or task-completion
- Module: Owls — DNA, evolution & routing
- Smell: No proactive initiative
- Pillar: proactivity
- Location: src/stackowl/owls/dna_defaults.py:6-8; dna_injector.py:16-50
- Evidence:
  ```python
  TRAIT_NAMES = ("challenge_level", "verbosity", "curiosity", "formality", "creativity", "precision")
  ```
- Why it's chatbot-like: The entire closed trait set governs register, skepticism, verbosity, citation style — how the owl *talks*. None increases task-completion drive/persistence/initiative, so a "tenacious" owl is indistinguishable from a "lazy" one; persistence lives only in the static global charter, un-tunable per owl.
- Fix direction: Add a persistence/initiative trait (e.g. `tenacity`/`completion_drive`) whose HIGH directive instructs pursuing the goal across blocked paths, wired into the attribution bands so it evolves from outcomes.
- Severity: S2

### F-53: Residual ask-first bias: `curiosity` is still framed as a clarify-gate, not an act-driver
- Module: Owls — DNA, evolution & routing
- Smell: Defers trivial decisions upward
- Pillar: proactivity
- Location: src/stackowl/owls/dna_injector.py:22-26
- Evidence:
  ```python
  ("curiosity", "When intent or scope is ambiguous but the most likely action is reversible, "
   "act on the most likely interpretation and state your assumption — ask the user a clarifying "
   "question only when the action is irreversible or expensive."),
  ```
- Why it's chatbot-like: The act-first flip was applied as text inside the `curiosity` directive, which only fires when `curiosity >= 0.70`. High curiosity still maps to the clarify/ask axis; a low-curiosity owl gets no act-first nudge, and the trait still anchors "ask the user."
- Fix direction: Decouple act-first from curiosity — make it an unconditional charter principle, and repurpose curiosity toward exploration breadth.
- Severity: S3

### F-54: Evolution learns only from successes — failures excluded by a positive-only directive
- Module: Owls — DNA, evolution & routing
- Smell: No learning from prior failures
- Pillar: learning loop
- Location: src/stackowl/owls/dna_attribution.py:139-146
- Evidence:
  ```python
  scored = [o for o in outcomes
      if o.quality_score is not None and o.dna_snapshot and o.success and not o.failure_class]
  ```
- Why it's chatbot-like: The attributor discards every failed outcome before bucketing, so DNA never learns which trait configs *cause* failures — it can only drift toward what already worked. A consistently-failing config is ignored, not penalized.
- Fix direction: Incorporate failed outcomes as negative signal (penalize high-failure bands), or let failure_class histograms steer deltas away from losing bands.
- Severity: S3

### F-55: Per-owl evolution failures are swallowed and counted as "stuck" with no retry
- Module: Owls — DNA, evolution & routing
- Smell: Gives up on failure
- Pillar: self-healing
- Location: src/stackowl/owls/evolution.py:354-359 (and 346-353)
- Evidence:
  ```python
  except Exception as exc:  # B5 — one owl's crash never sinks the batch
      log.engine.warning("[dna] coordinator._evolve_one_bounded: owl evolution failed — skipping", ...)
      return None
  ```
- Why it's chatbot-like: A crashed/timed-out owl is logged and dropped with no retry/backoff/remediation; LLM-fallback and attribution-query exceptions likewise return `{}` and give up. Evolution silently no-ops for the failing owl until the next nightly batch. (Batch-isolation is correct; per-owl recovery is absent.)
- Fix direction: Add bounded per-owl retry/backoff on transient errors and surface persistently-stuck owls for follow-up.
- Severity: S4

### F-56: Router still encodes a dedicated `clarify` verdict whose default-deny on delegated owls forces ask-up
- Module: Owls — DNA, evolution & routing
- Smell: Defers trivial decisions upward
- Pillar: proactivity
- Location: src/stackowl/owls/router.py:188-195; a2a_delegation.py:242-244
- Evidence:
  ```python
  "- 'clarify' ONLY when the request is genuinely ambiguous about WHAT to do AND the most "
  "likely action is expensive, slow, irreversible... between 'standard' and 'clarify', choose 'standard' and act."
  ```
- Why it's chatbot-like: The act-first guardrails are good, but `clarify` remains a first-class routing outcome and a2a default-denies clarify on delegated owls so the question must bubble through the parent to the user — asking the user is still a built-in escape hatch.
- Fix direction: Keep clarify only for irreversible/destructive ambiguity; add an explicit "attempt to resolve via tools (memory recall, cheap probe) before clarifying" step.
- Severity: S3

---

## Module: Parliament — multi-owl debate

### F-57: Single-pass synthesis with no verification the LLM honored the contract
- Module: Parliament — multi-owl debate
- Smell: No verification after action
- Pillar: self-healing
- Location: src/stackowl/parliament/synthesizer.py:124-147
- Evidence:
  ```python
  completion = await provider.complete(messages, model="")
  raw_text = completion.content
  parsed = self._parser.parse(raw_text, session.session_id)
  synthesis_text = self._format_synthesis_text(raw_text, session, confidence)
  ```
- Why it's chatbot-like: The synthesis call is single-shot — a malformed/empty completion is accepted and pushed into the parser with no re-prompt; the parser's fallback then dresses raw garbage as a verdict (consensus = first 200 chars).
- Fix direction: Detect the fallback path (no CONSENSUS/RECOMMENDATION markers) and re-prompt once stricter; treat persistently unparseable synthesis as degraded.
- Severity: S3

### F-58: Parser silently downgrades a contract violation into a fake verdict
- Module: Parliament — multi-owl debate
- Smell: Gives up on failure (log-and-return fallback)
- Pillar: self-healing
- Location: src/stackowl/parliament/synthesis_parser.py:69-89
- Evidence:
  ```python
  except Exception as exc:
      log.parliament.warning(... "parse failure — falling back to raw text" ...)
      return SynthesisResult(consensus=fallback_body[:200], disagreements=[],
                             recommendation="See synthesis above", ...)
  ```
- Why it's chatbot-like: A broad `except Exception` returns a SynthesisResult identical in type to a real one — the orchestrator marks the session `complete()` and stages pellets, so a parse failure never surfaces as degraded; the first 200 chars become "consensus."
- Fix direction: Carry an explicit `parse_ok=False`/degraded flag so the orchestrator marks `complete_no_synthesis` and skips pelletizing fabricated claims.
- Severity: S2

### F-59: Fabricated fallback "consensus" is staged into long-lived memory as a knowledge pellet
- Module: Parliament — multi-owl debate
- Smell: No verification after action
- Pillar: learning loop
- Location: src/stackowl/parliament/pellet_generator.py:108-145
- Evidence:
  ```python
  if synthesis.consensus: claims.append(synthesis.consensus)
  for claim in claims:
      await self._bridge.stage(fact)   # confidence=0.7, trust="self"
  ```
- Why it's chatbot-like: Pellet staging trusts `synthesis.consensus` unconditionally — when the parser fell back, the "consensus" is raw text truncated to 200 chars, yet persisted as a `confidence=0.7, trust="self"` durable fact, polluting long-term memory.
- Fix direction: Gate pelletization on a verified-parse flag and a confidence floor; never stage the fallback `body[:200]` consensus.
- Severity: S2

---

## Module: Scheduler — proactive jobs

### F-60: Recurring job marked permanently `failed` after 3 retries — never re-arms, no notification
- Module: Scheduler — proactive jobs
- Smell: Gives up on failure
- Pillar: self-healing / proactivity
- Location: src/stackowl/scheduler/scheduler.py:196-202, 236-240 (poll filter 84-88)
- Evidence:
  ```python
  if new_retries >= _MAX_RETRIES:
      log.heartbeat.error("[scheduler] %s: max retries reached — marking permanently failed", ...)
      await self._mark_failed(job)
  async def _mark_failed(self, job):
      await self._db.execute("UPDATE jobs SET status = 'failed' WHERE job_id = ?", (job.job_id,))
  ```
- Why it's chatbot-like: A daily proactive job (morning_brief, check_in) failing 3× transitions to terminal `failed`. The poll selects only `pending`, `recover()` re-arms only `pending`, `reap_stale_running` only touches `running` — the job never fires again, `next_run_at` is never recomputed. The proactive outreach goes dark.
- Fix direction: For a recurring schedule, `_mark_failed` should recompute `next_run_at` (re-arm next slot) and emit an operator notification/audit; reserve terminal `failed` for one-shots.
- Severity: S1

### F-61: `_mark_failed` writes no audit row and no notification — the outage is trace-only
- Module: Scheduler — proactive jobs
- Smell: Opaque / no trace
- Pillar: explainability / proactivity
- Location: src/stackowl/scheduler/scheduler.py:236-240
- Evidence:
  ```python
  async def _mark_failed(self, job):
      await self._db.execute("UPDATE jobs SET status = 'failed' WHERE job_id = ?", (job.job_id,))
  ```
- Why it's chatbot-like: Every other lifecycle transition calls `write_audit`, but the one that permanently kills a proactive job writes NO audit row and triggers NO notification — the only evidence is a single ERROR log line.
- Fix direction: Have `_mark_failed` call `write_audit(..., "job_failed", ...)` and route a proactive operator notification via the same delivery seam.
- Severity: S2

### F-62: Unknown-handler job marked terminally `failed`, not retained for late registration
- Module: Scheduler — proactive jobs
- Smell: No proactive initiative (registered≠reachable)
- Pillar: proactivity
- Location: src/stackowl/scheduler/scheduler.py:169-177
- Evidence:
  ```python
  handler = self._registry.get(job.handler_name)
  if handler is None:
      log.heartbeat.error("[scheduler] %s: unknown handler — marking failed", ...)
      await self._mark_failed(job)
      return
  ```
- Why it's chatbot-like: A seeded job whose handler is conditionally registered (e.g. `session_sweep`, `browser_recycle`) will, if registration is skipped or ordered after the first poll, be permanently `failed` on the first tick — unreachable forever even once the handler registers (terminal status).
- Fix direction: For a missing handler, leave the job `pending` (or back off) and warn so a later registration recovers it; reserve `failed` for handler-raised errors past max-retries.
- Severity: S2

### F-63: Idempotency claim can pin a recurring occurrence at a past instant
- Module: Scheduler — proactive jobs
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/scheduler/scheduler.py:128-139
- Evidence:
  ```python
  already = await self._db.fetch_all(
      "SELECT status FROM job_runs WHERE idempotency_key = ? AND status = 'completed'", (occurrence_key,))
  if already:
      log.heartbeat.info("[scheduler] %s: idempotent skip", ...)
      return  # job left pending; next_run_at NOT advanced here
  ```
- Why it's chatbot-like: On a deduped/lost-race occurrence the poller returns early WITHOUT advancing `next_run_at`; the job stays `pending` at a past instant and idempotent-skips every poll, performing no verification that the next occurrence is actually scheduled. (Mitigated: the normal single-dispatch path advances the slot.)
- Fix direction: On idempotent skip, recompute/advance `next_run_at` (or assert it is in the future).
- Severity: S3

---

## Module: Channels — adapters & delivery

### F-64: Slack on-turn reply silently dropped on transport failure
- Module: Channels — adapters & delivery
- Smell: Gives up on failure (send error swallowed)
- Pillar: self-healing
- Location: src/stackowl/channels/slack/adapter.py:904-916 (called from `send_text` :326)
- Evidence:
  ```python
  try:
      await client.chat_postMessage(**post_kwargs)
  except Exception as err:  # noqa: BLE001
      log.slack.error("[slack] adapter._post_text: post failed", exc_info=err, ...)
  # function ends — no re-raise, returns success-shaped
  ```
- Why it's chatbot-like: `send_text` carefully raises `DeliveryError` on a *target* miss, but the actual transport delegates to `_post_text`, which catches every `chat_postMessage` failure (network, ratelimited, auth, channel_not_found), logs, and returns success-shaped. The reply silently never arrives, the ledger sees a clean send, no retry.
- Fix direction: On the on-turn path `_post_text` should re-raise (or return a failure flag `send_text` raises into `DeliveryError`) so the deliverer records `failed`/retries; add bounded retry on `ratelimited`.
- Severity: S1

### F-65: Telegram no-target file send is a silent return contradicting the documented contract
- Module: Channels — adapters & delivery
- Smell: Gives up on failure (delivery silently dropped)
- Pillar: self-healing
- Location: src/stackowl/channels/telegram/adapter.py:628-660 (contract base.py:155-167)
- Evidence:
  ```python
  if self._bot_app is None or target is None:
      log.telegram.warning("[telegram] adapter.send_file: no active chat — file dropped", ...)
      return
  ```
- Why it's chatbot-like: The docstring promises the deliverer surfaces undeliverable as `failed`, but a `None` target/bot is a silent `return` — the file vanishes with only a warning, never reaching the ledger as `failed`. (The upload error itself does propagate here — but see F-66.)
- Fix direction: For the no-target case raise/return a structured failure the deliverer maps to `failed`, not a bare `return`.
- Severity: S2

### F-66: Discord/WhatsApp file-upload transport failure swallowed (no retry, no ledger signal)
- Module: Channels — adapters & delivery
- Smell: Gives up on failure (delivery error swallowed)
- Pillar: self-healing
- Location: src/stackowl/channels/discord/adapter.py:300-309; whatsapp/adapter.py:344-352
- Evidence:
  ```python
  try:
      await channel.send(caption or None, file=discord.File(file_path))
  except Exception as exc:  # self-healing — a file send must not crash the turn
      log.discord.error("[discord] adapter.send_file: upload failed", exc_info=exc, ...)
  # no re-raise → caller sees success
  ```
- Why it's chatbot-like: "Must not crash the turn" is conflated with "must not be reported." The upload to a fully-resolved live channel can fail (size limit, permissions, network) and the user never gets the file, yet the deliverer records a clean send — inconsistent with Telegram which lets it propagate.
- Fix direction: After logging, re-raise so `ProactiveDeliverer` maps to `failed`, or return a status the caller surfaces; optionally retry once and fall back to a text link.
- Severity: S2

---

## Module: Gateway — turn routing & clarify

### F-67: Reaped wedged turn drops its objective — no goal resume
- Module: Gateway — turn routing & clarify
- Smell: Goal amnesia across turns
- Pillar: goal persistence / self-healing
- Location: src/stackowl/gateway/turn_registry.py:650-687
- Evidence:
  ```python
  if done and (turn.status is not TurnStatus.DONE or expired):
      await self.deregister(rid)        # turn.original_input vanishes here
      reaped.append(rid)
  if reaped and self._on_reaped is not None:
      self._on_reaped(reaped)           # eviction, not re-dispatch
  ```
- Why it's chatbot-like: A turn that wedges (task done but never reached DONE) is silently deregistered and its `original_input` discarded. The post-reap hooks only free the slot and evict the parked message — neither re-dispatches the wedged turn's own goal. The user's request evaporates.
- Fix direction: On reap of a running turn, re-enqueue `turn.original_input` as a fresh queued-new intake (with a retry guard / "hit a snag, retrying" notice) instead of only evicting.
- Severity: S1

### F-68: Clarify timeout bounces a defaultable decision to the model with no auto-resume
- Module: Gateway — turn routing & clarify
- Smell: Defers trivial decisions upward
- Pillar: proactivity
- Location: src/stackowl/tools/interaction/clarify.py:56-60, 237-241
- Evidence:
  ```python
  _TIMED_OUT = ("The user did not reply in time to your question ({question!r}). If this was "
      "gating a consequential action, ABORT... otherwise proceed with your best assumption and state it.")
  return self._ok(_TIMED_OUT.format(question=question), t0, extra={"timed_out": True})
  ```
- Why it's chatbot-like: On timeout there is no auto-resume with a server-side default even when the clarify carried explicit `choices` — it punts the whole decision back into the prompt as free-text and waits the full 30-min TTL.
- Fix direction: When `choices` has a single obvious/safe option, resolve it as a default; on timeout, auto-resume with the stated default for reversible actions. (Mitigated by ABORT-on-consequential contract.)
- Severity: S3

### F-69: Clarify tool always blocks/parks — no code-side "act on most likely interpretation"
- Module: Gateway — turn routing & clarify
- Smell: No proactive initiative
- Pillar: proactivity
- Location: src/stackowl/tools/interaction/clarify.py:200-213
- Evidence:
  ```python
  clarify_id = await gateway.ask(str(session_id), str(channel), question,
      choices=choices, awaiting_text=awaiting_text, blocking=True)
  answer, outcome = await gateway.wait_for_answer(clarify_id, timeout=self._timeout_s)
  ```
- Why it's chatbot-like: The tool unconditionally delivers and parks; the "only clarify when irreversible, else act and state assumption" rule lives ONLY in the description string (advisory). A model that over-asks always parks the turn up to 30 min. (Mitigated: non-interactive contexts short-circuit.)
- Fix direction: Add a pre-park gate that, for reversible gates with a clear most-likely choice, returns "proceeding with assumption X" without parking.
- Severity: S4

---

## Module: Interaction — clarify/consent/instincts

### F-70: Reversible cost-pause defers a trivial "Continue?" to the user instead of a sane default
- Module: Interaction — clarify/consent/instincts
- Smell: Defers trivial decisions upward
- Pillar: proactivity
- Location: src/stackowl/interaction/cost_pause.py:117-192, 210-220
- Evidence:
  ```python
  question = f"This turn has spent about ${cost:.2f} so far. Continue?"
  clarify_id = await gateway.ask(session_id, channel, question,
      choices=(_CHOICE_CONTINUE, _CHOICE_STOP), blocking=True)
  ```
- Why it's chatbot-like: A *soft* per-turn budget crossing (the hard cap is separate) blocks the whole turn to ask a yes/no the assistant could decide itself given the daily cap still protects the user. Every soft crossing bounces an interrupt mid-task.
- Fix direction: Default to continue-and-notify for reversible spend under the hard cap (the machinery already fails OPEN to Continue), reserving the blocking ask for spend near the hard limit.
- Severity: S2

### F-71: Clarify gateway has no instinct/auto-answer path — every ambiguity parks a turn on the human
- Module: Interaction — clarify/consent/instincts
- Smell: Defers trivial decisions upward
- Pillar: proactivity
- Location: src/stackowl/interaction/clarify_gateway.py:142-220
- Evidence:
  ```python
  async def ask(self, session_id, channel, question, *, choices=(), ..., blocking=False, deliver=True):
      ...
      self._pending[clarify_id] = PendingClarify(...)
  ```
- Why it's chatbot-like: `ask` unconditionally registers a pending question and routes it to the user — no pre-ask gate that tries a default/instinct for a reversible, low-risk choice. Every caller that hits ambiguity stops and waits.
- Fix direction: Add an instinct/default-resolution layer in front of `ask` that auto-answers reversible/trivial clarifies (logged, undoable default) and only parks high-stakes questions.
- Severity: S2

### F-72: Intent classifier is stateless across turns — no learning from prior misclassifications
- Module: Interaction — clarify/consent/instincts
- Smell: No learning from prior failures
- Pillar: learning loop
- Location: src/stackowl/interaction/intent_classifier.py:152-200, 436-482
- Evidence:
  ```python
  except Exception as exc:  # self-healing — a verdict call must never raise
      ...  return True   # fail-safe to answer
  ```
- Why it's chatbot-like: Each verdict is a stateless single-pass call; a prior misclassification (e.g. a pivot wrongly swallowed as an answer) is never recorded or fed back, so the same ambiguous reply is re-misclassified identically. (Fail-safe directions are honest, but logs are never consumed to adapt.)
- Fix direction: Persist verdict outcomes (and user corrections/observed pivots) and bias future classifications, or surface a low-confidence "I assumed X — say 'no' to switch."
- Severity: S3

---

## Module: Supervisor — turn progress tracking

### F-73: Stuck-detection without progress-driving — supervisor restarts blindly, never nudges toward the goal
- Module: Supervisor — turn progress tracking
- Smell: No proactive initiative
- Pillar: proactivity
- Location: src/stackowl/supervisor/supervisor.py:90-135
- Evidence:
  ```python
  await state.task.run()
  state.consecutive_failures = 0
  ...
  await self._clock.async_sleep(backoff)
  backoff = min(backoff * 2, _BACKOFF_MAX)
  ```
- Why it's chatbot-like: The supervisor only reacts — loops, restarts on crash, backs off. It has no notion of a goal or progress; a task that runs forever doing nothing (live-but-stuck, never raises/returns) is treated as healthy. No nudge, no escalation.
- Fix direction: Add a per-task liveness/progress heartbeat and actively probe/escalate stuck-but-not-crashed tasks, mirroring the `TurnProgressTracker` in pipeline/progress_tracker.py.
- Severity: S3

### F-74: Give-up floor on max consecutive failures with no self-healing escalation
- Module: Supervisor — turn progress tracking
- Smell: Gives up on failure
- Pillar: self-healing
- Location: src/stackowl/supervisor/supervisor.py:114-132
- Evidence:
  ```python
  if state.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
      log.startup.error("[supervisor] %s: max consecutive failures reached — marking failed", ...)
      state.status = "failed"
      return
  ```
- Why it's chatbot-like: After 5 failures the task is marked `failed` and the loop returns permanently — no alternate strategy, no alert/handoff, no repair attempt. It stays dead until process restart; `health()` reports `failed` but nothing acts. (A defensible honest floor, but it should escalate.)
- Fix direction: Escalate — emit a recoverable signal / notify an operator / attempt degraded-mode restart rather than silently parking the task dead.
- Severity: S3

### F-75: No verification that a "completed" task actually did useful work
- Module: Supervisor — turn progress tracking
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/supervisor/supervisor.py:99-106
- Evidence:
  ```python
  await state.task.run()
  state.consecutive_failures = 0
  ...
  log.startup.debug("[supervisor] task: completed cleanly — restarting", ...)
  ```
- Why it's chatbot-like: A clean return is treated as success and the failure counter resets, with zero outcome verification; a task that returns immediately doing nothing loops forever resetting the counter — no tight-loop guard on sub-ms clean returns.
- Fix direction: Verify post-condition / minimum work-done, or guard against rapid-clean-return spin.
- Severity: S4

---

## Module: Notifications & brief — proactive surface

### F-76: `/urgent` reports "delivered" without ever transporting to a channel
- Module: Notifications & brief — proactive surface
- Smell: No verification after action
- Pillar: tool execution / self-healing
- Location: src/stackowl/commands/urgent_command.py:117-148; notifications/router.py:108, 261-264, 300-311
- Evidence:
  ```python
  # urgent_command — counts a routing DECISION as a send
  else: delivered += 1
  return f"urgent: broadcast to {delivered} channels"
  # router.deliver — "Never touches a channel adapter — real transport lands in Epic 8/9."
  if decision == "delivered": delivered_at = now   # writes 'delivered' audit row, no send
  ```
- Why it's chatbot-like: A `critical` user broadcast claims "broadcast to N channels" and writes a `delivered` audit row, but `NotificationRouter.deliver` only makes a routing decision and explicitly never transports — nothing reaches the user.
- Fix direction: Route `/urgent` through `ProactiveDeliverer.deliver` (the transport seam) and derive the count from real `DeliveryStatus`, not absence of exception.
- Severity: S1

### F-77: `notification_digest` handler has no producer — batched notifications never flush
- Module: Notifications & brief — proactive surface
- Smell: No proactive initiative (registered≠reachable)
- Pillar: proactivity
- Location: src/stackowl/notifications/digest_job.py:66-76 vs scheduler/assembly.py (no seed)
- Evidence:
  ```python
  @property
  def handler_name(self) -> str:
      return "notification_digest"
  # grep notification_digest across scheduler/assembly.py + startup/* → NO seed row
  ```
- Why it's chatbot-like: Every `batched`/quiet-hours notification is persisted to `notification_queue` to flush "later," but no `jobs` row seeds the `notification_digest` poll — batched messages silently age forever. The batch-surface machinery is built but dormant.
- Fix direction: Seed a recurring `notification_digest` job in `SchedulerAssembly`, or have `wiring_audit` flag this seeded-handler-with-no-row as dangling.
- Severity: S1

### F-78: EventDeliveryBridge is permanently dormant — empty allow-list, no event-driven proactivity
- Module: Notifications & brief — proactive surface
- Smell: No proactive initiative (dormant machinery)
- Pillar: proactivity
- Location: src/stackowl/notifications/event_bridge.py:44, 63-85
- Evidence:
  ```python
  _ALLOWED_EVENTS: frozenset[str] = frozenset()
  def register(self, bus):
      if not _ALLOWED_EVENTS:
          log.notifications.info("...no proactive bus events to subscribe — bridge dormant...")
          return
  ```
- Why it's chatbot-like: The one event→notification bridge that would let the system surface things unprompted subscribes to nothing; all proactivity comes from cron seeds — no live publisher-driven "anticipate and ping." (Mitigated: honest/intentional, logs a clean dormant state.)
- Fix direction: Add at least one genuinely bus-native proactive event with a declared publisher, or document the bridge as deferred.
- Severity: S3

### F-79: Brief assemblers omit-on-empty off a single hardcoded recall query — reports state, doesn't anticipate
- Module: Notifications & brief — proactive surface
- Smell: No proactive initiative
- Pillar: proactivity
- Location: src/stackowl/brief/assemblers.py:140-156, 178-195
- Evidence:
  ```python
  records = await self._bridge.recall(_RECALL_QUERY, limit=_MAX_HIGHLIGHTS)   # "recent important facts"
  if not records:
      log.scheduler.debug("[brief] memory_highlights.assemble: no records — omitting", ...)
      return BriefSection(..., items=[], omitted=True)
  ```
- Why it's chatbot-like: The brief never proposes next actions — fixed sections off a single hardcoded query; empty recall silently shrinks it to a canned scheduler-status readout with no "why nothing to highlight."
- Fix direction: Drive highlights from the user's actual recent activity/goals, surface an explicit "nothing notable" item, log empty-recall at `info`.
- Severity: S3

---

## Module: Messaging — heartbeat & proactive msgs

> NO FINDINGS. `src/stackowl/messaging/` contains only `a2a.py` (A2AMessage + A2AQueue) —
> a synchronous request/response delegation transport between owls with bounded timeouts,
> backpressure health checks, and orphan reaping. Not a heartbeat/user-facing proactive
> surface (that lives in scheduler/handlers/check_in.py + notifications). No dormant/unwired
> proactive path or silent-drop under this audit's lens.

---

## Module: CLI & commands

### F-80: `/connect` reports "connected" without confirming the connection
- Module: CLI & commands
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/commands/connect_command.py:116-130
- Evidence:
  ```python
  try:
      await adapter.connect()
      return f"{service} connected."
  except Exception as exc:
      return f"Failed to connect {service}: {exc}"
  ```
- Why it's chatbot-like: Success is inferred solely from `connect()` not raising. The adapter exposes a cheap `is_connected()` (gmail.py:92) that is never called to confirm tokens persisted — a silent OAuth/token-save miss still prints "connected." (The list path *does* use `is_connected()`, making the omission inconsistent.)
- Fix direction: After `connect()`, call `await adapter.is_connected()` and only claim success if true; else return an honest "flow finished but credentials not detected."
- Severity: S2

### F-81: State-changing config/provider writes claim success without confirming the write landed
- Module: CLI & commands
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/commands/provider_command.py:286-296 (also config_command.py:204-213, 229-233)
- Evidence:
  ```python
  providers.append(entry)
  save_yaml(path, data)
  self._emit_reloaded(name)
  return f"✓ Provider '{name}' added{key_note} — applies on the next reload/restart"
  ```
- Why it's chatbot-like: `save_yaml(...)` is fire-and-forget; the handler returns `✓ ... added` without re-reading the file to confirm the entry persisted/parses. A partial write or permission issue still prints the checkmark. (Mitigated: schema validation *before* the write; effect honestly deferred to "next reload/restart.")
- Fix direction: After `save_yaml`, reload and assert the mutation is present before emitting `✓`.
- Severity: S2

---

## Module: MCP & integrations

### F-82: MCP `call_tool` connection failures silently return empty string as success
- Module: MCP & integrations
- Smell: Gives up on failure
- Pillar: self-healing
- Location: src/stackowl/mcp/client.py:112-136 (call site 100-110)
- Evidence:
  ```python
  async def _invoke_tool(self, config, tool_name, args) -> str:
      try:
          ...  return _extract_content(await session.call_tool(tool_name, args))
      except Exception as exc:
          log.error("mcp.client._invoke_tool: call failed", exc_info=exc, ...)
          return ""
  ```
- Why it's chatbot-like: Any MCP server connection/init/call failure is swallowed and returned as `""` — no retry, no fallback, no liveness re-probe before invocation (unlike web_search which retries-then-cascades). The error is logged but never surfaced to the caller.
- Fix direction: Return a typed failure (raise or a result with an error field); add retry-once and a pre-call liveness re-probe via the existing `McpLivenessProbe`.
- Severity: S1

### F-83: McpTool reports `success=True` with empty output when the underlying call failed
- Module: MCP & integrations
- Smell: No verification after action
- Pillar: tool execution
- Location: src/stackowl/mcp/_tool.py:103-128
- Evidence:
  ```python
  result_str = await self._client.call_tool(self._server_config, self._definition.name, dict(kwargs))
  return ToolResult(success=True, output=result_str, duration_ms=duration_ms)
  ```
- Why it's chatbot-like: Because `call_tool` catches everything and returns `""`, the `except` here is effectively dead and a failed/blocked call yields `ToolResult(success=True, output="")` — empty output treated as a successful answer rather than a retry/failure signal.
- Fix direction: Have `call_tool` propagate failures (F-82) and in `execute` distinguish genuine empty-but-successful from a swallowed error; mark `success=False` with an actionable error when transport failed or server blocked.
- Severity: S2

### F-84: MCP tool discovery dead-ends to empty list on connect failure and caches it
- Module: MCP & integrations
- Smell: Gives up on failure
- Pillar: self-healing
- Location: src/stackowl/mcp/client.py:54-82 (cache put 49-50)
- Evidence:
  ```python
  except Exception as exc:
      log.error("mcp.client._fetch_tools: connection failed", exc_info=exc, ...)
      return []
  ```
- Why it's chatbot-like: A transient connection failure during discovery returns `[]`, which is then `cache.put`, so a one-off network blip persists as "this server has no tools" for the cache TTL. No retry, no error propagation.
- Fix direction: Retry discovery once on transport failure; do not cache an empty result that came from an exception path (cache only genuine empty tool-lists).
- Severity: S2

---

## Module: Setup, health & service

### F-85: Watchdog pings systemd alive on a blind timer with no liveness/health gating
- Module: Setup, health & service
- Smell: No verification after action
- Pillar: self-healing
- Location: src/stackowl/service/watchdog.py:105-117 (`_sd_notify` 119-137)
- Evidence:
  ```python
  async def _ping_loop(self, interval_s):
      while True:
          await asyncio.sleep(interval_s)
          self._sd_notify("WATCHDOG=1")
  ```
- Why it's chatbot-like: The watchdog exists to detect a wedged process, but it pings "alive" unconditionally as long as the asyncio loop turns — never consulting HealthAggregator/any liveness signal. A process whose pipeline/adapters deadlocked but whose loop still spins keeps reporting healthy, defeating systemd restart-on-watchdog-timeout.
- Fix direction: Gate `WATCHDOG=1` on a real liveness check (HealthAggregator.collect / message-loop heartbeat) and skip the ping when a critical subsystem is down.
- Severity: S2

### F-86: Reachability census is built but never run at boot — dead-on-default-path subsystems undetected
- Module: Setup, health & service
- Smell: No proactive initiative
- Pillar: proactivity / verification
- Location: src/stackowl/health/reachability/census.py:62-87 (only test callers)
- Evidence:
  ```python
  async def run_census() -> list[ProbeResult]:
      """Run every registered probe; a probe that raises → unreachable (fail-closed)."""
  def census_passes(results) -> bool:
      return all(r.reachable for r in results)
  ```
- Why it's chatbot-like: A fail-closed self-audit that verifies the default agent path is wired exists, but `StartupOrchestrator.run` never calls it — a subsystem that goes dark on the default path boots "successfully" and is only caught if a human runs the test suite.
- Fix direction: Invoke `run_census()` as a boot phase and refuse READY / emit a loud degraded alert when `census_passes` is False.
- Severity: S2

### F-87: Health is detect-only and on-demand — ResilienceContributor never wired, no heal trigger
- Module: Setup, health & service
- Smell: Gives up on failure / No proactive initiative
- Pillar: self-healing / proactivity
- Location: src/stackowl/health/aggregator.py:34-54; cli/app.py:322-348
- Evidence:
  ```python
  # cli/app.py — "ResilienceContributor needs live HealableResource refs from inside
  # `stackowl serve` ... Wiring here would just report 'no resources registered'."
  statuses = asyncio.run(agg.collect())
  if any(s.status != "ok" for s in statuses): sys.exit(1)
  ```
- Why it's chatbot-like: The only path that runs HealthAggregator is the out-of-process `stackowl health` CLI — a human must ask. On "down"/"degraded" it prints icons and exits 1; it never recycles a resource, retries, or alerts. Health DETECTS only; nothing TRIGGERS a heal.
- Fix direction: Add an in-process periodic health sweep (scheduler job) wiring `ResilienceContributor` + live resources, and on down/degraded recycle via `attempt_with_recycle` or push a proactive operator alert.
- Severity: S2

### F-88: Mono-role boot has no crash supervision — a recoverable crash of the single process just dies
- Module: Setup, health & service
- Smell: Gives up on failure
- Pillar: self-healing
- Location: src/stackowl/startup/orchestrator.py:2856-2859 (`_supervise_core` gated to `gateway` role)
- Evidence:
  ```python
  if self._role == "gateway":
      asyncio.create_task(_supervise_core(core_proc_holder, gateway_socket_path, stop_event))
  ```
- Why it's chatbot-like: The capped-backoff crash-respawn self-heal only exists in the split `gateway` role. In the default `mono` role an unhandled crash propagates up `run()` as `StartupError` and the process exits, relying on an external systemd/launchd restart that may not be installed (installer is opt-in).
- Fix direction: Always run a process/subsystem supervisor (not just `gateway`), or have `mono` boot verify a native service manager with `Restart=always` and warn loudly if absent.
- Severity: S2
