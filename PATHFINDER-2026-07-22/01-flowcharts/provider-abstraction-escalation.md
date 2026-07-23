# Provider Abstraction + Escalation + Resilience

## Sources consulted

- `src/stackowl/providers/llm_gateway.py` (full, 389 lines)
- `src/stackowl/providers/circuit_breaker.py` (full, 337 lines)
- `src/stackowl/providers/_resilient_round.py` (full, 387 lines)
- `src/stackowl/providers/registry.py` (full, 827 lines)
- `src/stackowl/providers/tier_selector.py` (full, 67 lines)
- `src/stackowl/providers/base.py` (full, 410 lines)
- `src/stackowl/pipeline/recovery_actuator.py:1-80`
- `src/stackowl/memory/retry_queue_store.py` (grep sweep)
- `src/stackowl/pipeline/steps/execute.py:1895-1949`

## Concrete findings

**Tier cascade** (`LLMGateway.complete_with_tools`, `llm_gateway.py:228-362`): floor→ceiling walk over `LADDER=("fast","standard","powerful")`, sliced by `tier_span()`. Each tier resolves via `ProviderRegistry.resolve_tier_with_fallback`. A non-tool-capable tier is skipped upward when `can_escalate` (true except at the ceiling).

**`_retry_same_tier_once` vs cascade**: on a classified/cascadable fault, gated by a config flag and "not yet retried this tier," fires exactly ONE same-tier retry via `RecoveryActuator` BEFORE cascading. Falls through to cascade/re-raise if that also fails.

**CircuitBreaker**: 3 states CLOSED/OPEN/HALF_OPEN. Trips OPEN after `failure_threshold` (default 3) within `window_seconds` (default 60). Auto-promotes OPEN→HALF_OPEN after elapsed backoff. HALF_OPEN admits exactly 1 probe; success→CLOSED+reset backoff; failure→OPEN+**doubles** backoff (cap 900s). **State is per-provider (keyed by config name), NOT per-model** — all models under one provider share one breaker.

**`resilient_round()`**: does NOT retry itself — wraps exactly ONE remote round: breaker gate (no I/O) → rate-limiter acquire → execute once → classify outcome → record onto breaker (+ `limiter.penalize()`/`breaker.open_for()` on RATE_LIMIT) → **always re-raises**. Pure classification + bookkeeping around one attempt.

**Is the provider layer blind to upper layers? YES — architecturally isolated.** What crosses the `ProviderRegistry`/`LLMGateway` boundary: `user_text`, `system_text`, `tool_schemas`, `tool_dispatcher`, `floor`/`ceiling` labels, a static `purpose` string, `history`, `wrapup_deadline_fn`, `on_escalate`. None of this encodes "this is attempt N of an outer retry," an owl's failure history, or "this request already failed via a different mechanism":
- `_retry_same_tier_once` builds a brand-new `Failure(attempt=1)` every call — never receives an outer attempt counter.
- `RetryQueueStore` (app/goal-level, capped @3, exponential backoff) never passes its `attempt_count`/`banned_capabilities` down into the provider layer.
- `CircuitBreaker` is keyed purely by provider name — no trace_id/turn/owl/session concept.
- The ONLY thing crossing the boundary that survives is `trace_id`, used exclusively for cost accounting and log correlation — **never read by any retry/circuit-breaker decision.**

Every layer (SDK auto-retry, resilient_round's classify+record, CircuitBreaker's backoff, LLMGateway's same-tier-retry + cascade, RetryQueueStore's goal-level backoff) decides from its OWN local state only. No shared "attempt N across the whole stack" signal exists anywhere.

## Distinct retry/circuit-breaking mechanisms at THIS layer (excludes app-level RetryQueueStore)

1. SDK-level auto-retry (documented, not in-repo) — `base.py:363-367`
2. `resilient_round()`'s single-attempt breaker gate + classify + record — `_resilient_round.py:233-387`
3. CircuitBreaker HALF_OPEN adaptive backoff (doubles, cap 900s) — `circuit_breaker.py:277-297,319-331`
4. CircuitBreaker `open_for` quota-aware cooldown (parsed Retry-After) — `circuit_breaker.py:197-248`
5. `LLMGateway._retry_same_tier_once` — one immediate same-tier retry — `llm_gateway.py:119-139`
6. `LLMGateway` tier cascade (implicit "retry on a different provider") — `llm_gateway.py:156-224,269-362`
7. `ProviderRegistry.get_with_cascade`/`TierSelector` — round-robin among healthy providers WITHIN a tier, skipping OPEN breakers — `registry.py:485-652`
8. `RateLimiter.penalize()` — back-pressure penalty, separate from the breaker — `_resilient_round.py:337-345`

## Mermaid

```mermaid
flowchart TD
    A["execute.py:1937 gateway.complete_with_tools()"] --> B["llm_gateway.py:269 tier_span(floor,ceiling)"]
    B --> C{"llm_gateway.py:272 for idx,tier in tiers"}
    C --> D["registry.py:609 resolve_tier_with_fallback(tier)"]
    D --> D1["registry.py:485 get_with_cascade — skip OPEN breakers,\ntier_selector.py:30 round-robin healthy providers"]
    D1 --> E{"llm_gateway.py:283 supports_tools?"}
    E -- "no, can_escalate" --> C
    E -- yes --> F["llm_gateway.py:295 provider.complete_with_tools()"]
    F --> G["base.py:122 _resilient_round() bracket"]
    G --> H["_resilient_round.py:233 resilient_round()"]
    H --> H1{"circuit_breaker.py:90 breaker.state"}
    H1 -- OPEN --> H2["raise CircuitOpenError\n_resilient_round.py:276"]
    H1 -- HALF_OPEN --> H3["circuit_breaker.py:177 admit_probe() — 1 in flight max"]
    H1 -- CLOSED/admitted --> H4["rate_limiter.acquire()\n_resilient_round.py:299"]
    H4 --> H5["do_round() — actual HTTP call\n(SDK auto-retries 5xx/conn internally)"]
    H5 -- success --> H6["circuit_breaker.py:158 record(ok=True)\nHALF_OPEN→CLOSED reset backoff"]
    H5 -- fault --> H7["_resilient_round.py:90 classify_failure_cause()"]
    H7 --> H8["circuit_breaker.py:277 record(ok=False)\nCLOSED→OPEN @ threshold,\nHALF_OPEN→OPEN doubles backoff (cap 900s)"]
    H7 -- RATE_LIMIT --> H9["limiter.penalize() +\nbreaker.open_for(cooldown)\n_resilient_round.py:337-360"]
    H8 --> I["exception re-raised — resilient_round NEVER retries itself"]
    H9 --> I
    I --> J["llm_gateway.py:300 except BaseException"]
    J --> K{"is_cascadable_fault(exc)\n&& flag on && not yet retried\nllm_gateway.py:305-309"}
    K -- yes --> L["llm_gateway.py:119 _retry_same_tier_once()\nvia RecoveryActuator — ONE immediate retry,\nfresh Failure(attempt=1), no outer context"]
    L -- recovered --> M["result set → escalate-check, return"]
    L -- still fails --> N{"can_escalate &&\nis_cascadable_fault\nllm_gateway.py:330"}
    K -- no --> N
    N -- yes --> O["log + on_escalate() reset ledger\ncontinue to next tier — llm_gateway.py:349-351"]
    O --> C
    N -- no (ceiling or non-fault) --> P["raise — turn ends unrecoverable\nllm_gateway.py:341"]
    F -- ESCALATE sentinel --> O2["llm_gateway.py:352 discard + step up tier"]
    O2 --> C
    C -- tiers exhausted --> Q["return final_text, calls\n(possibly empty floor)"]
    M --> Q

    subgraph ISOLATED["Layer with NO context back into provider layer"]
        R["memory/retry_queue_store.py:136 RetryQueueStore\ngoal-level, DB-backed, capped @3,\nexponential re-arm delay — polled every 1min"]
    end
    R -.->|"only trace_id shared,\nused for cost/logs only — never read here"| H5
```

## Confidence note + known gaps

High confidence — all core claims grounded in full-file reads of the five entry points plus registry/tier_selector/recovery_actuator. Did not fully read `rate_limiter.py` or `retry_queue_store.py` end-to-end (grep-sufficient to confirm architectural separation). Did not trace `openai_provider.py`/`anthropic_provider.py`'s internal ReAct-loop iteration structure in detail (covered by the execute-step sibling).
