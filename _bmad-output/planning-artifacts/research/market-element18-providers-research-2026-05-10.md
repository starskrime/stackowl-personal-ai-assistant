---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments:
  - _bmad-output/planning-artifacts/element18-providers-audit-2026-05-10.md
workflowType: 'research'
lastStep: 6
research_type: 'market'
research_topic: 'StackOwl Element 18 — Providers layer (cost-aware routing, health monitoring, multi-model orchestration, capability-based dispatch)'
research_goals: '10-section competitive analysis mapped to StackOwl gaps P1–P10, with risk register R1–R10 for Winston architecture phase'
user_name: 'Boss'
date: '2026-05-10'
web_research_enabled: true
source_verification: true
---

# Market Research: StackOwl Element 18 — Providers Layer

**Date:** 2026-05-10  
**Research Type:** Market Research  
**Scope:** 10 sections covering production multi-provider routing, cost-aware routing SOTA, health monitoring/failover, capability-based dispatch, config schema design, hardcoded model name anti-patterns, provider deduplication, cost tracking/attribution, and provider security — each mapped to StackOwl's verified gaps P1–P10.

---

## Section 1 — Production Multi-Provider Routing Systems

### Landscape Overview (2025–2026)

The multi-provider LLM routing space consolidated into three distinct layers in 2025–2026:

| System | Type | Routing Strategy | Health Check | Failover | Config Schema |
|---|---|---|---|---|---|
| **LiteLLM** | Open-source proxy | Static/latency/usage/cost-based | Passive (error detection) + optional active probe | `fallbacks:` ordered list per model group | YAML `model_list` with `litellm_params` |
| **PortKey** | Managed + OSS gateway | Conditional/fallback/loadbalance modes | Continuous — marks targets "open" when failing | Circuit-breaker style; graceful degradation | JSON `strategy.mode` + `targets[]` |
| **OpenRouter** | SaaS marketplace | Provider-transparent | Passive (auto-reroutes on error) | Immediate transparent switch | API key only; per-request `model` param |
| **Martian** | Router-as-a-service | Quality+cost optimization per prompt | Passive + automatic reroute on outage | Automatic to next-best model | Single endpoint; routing opaque |
| **Unify.ai** | Hosted router | Quality/cost/speed per-prompt | Not published | Not published | OpenAI-compat API |
| **AWS Bedrock** | Cloud managed | Region/capacity-aware | Managed by AWS | Cross-region failover | AWS config profiles |
| **Azure AI** | Cloud managed | Deployment-based | Managed by Azure | PTU → pay-as-you-go fallback | Azure deployment config |
| **Vertex AI** | Cloud managed | Model Garden routing | Managed by Google | Not configurable | GCP project config |
| **Bifrost (Maxim)** | OSS high-perf | Multi-strategy | Active + passive; <11µs overhead | OPEN/HALF-OPEN circuit breaker | JSON config |

**Sources:** [PortKey vs LiteLLM vs OpenRouter 2026 (pkgpulse.com)](https://www.pkgpulse.com/guides/portkey-vs-litellm-vs-openrouter-llm-gateway-2026), [Top 5 LLM Gateways 2025 (helicone.ai)](https://www.helicone.ai/blog/top-llm-gateways-comparison-2025), [Best LLM Router and AI Gateway 2026 (inworld.ai)](https://inworld.ai/resources/best-llm-router-ai-gateway), [Top 5 LLM Router Solutions 2026 (getmaxim.ai)](https://www.getmaxim.ai/articles/top-5-llm-router-solutions-in-2026/)

### LiteLLM Config Schema (Canonical Example)

```yaml
model_list:
  - model_name: "claude-sonnet"          # alias received
    litellm_params:
      model: "anthropic/claude-sonnet-4-6"
      api_key: "${ANTHROPIC_API_KEY}"
      rpm: 100

  - model_name: "gpt-fallback"
    litellm_params:
      model: "gpt-4o"
      api_key: "${OPENAI_API_KEY}"

router_settings:
  routing_strategy: "latency-based-routing"   # or: cost-based-routing
  num_retries: 3
  timeout: 30
  fallbacks:
    - {"claude-sonnet": ["gpt-fallback"]}
  default_fallbacks: ["gpt-fallback"]
  context_window_fallbacks:
    - {"claude-sonnet": ["gpt-4o-mini"]}
```

**Source:** [LiteLLM Proxy Config Docs (docs.litellm.ai)](https://docs.litellm.ai/docs/proxy/configs), [LiteLLM Fallbacks (docs.litellm.ai)](https://docs.litellm.ai/docs/proxy/reliability)

### PortKey Config Schema (Conditional Routing)

```json
{
  "strategy": {
    "mode": "conditional",
    "conditions": [
      {
        "query": { "metadata.request_type": { "$eq": "code" } },
        "then": "target-codex"
      }
    ],
    "default": "target-claude"
  },
  "targets": [
    { "virtualKey": "anthropic-key", "overrideParams": { "model": "claude-sonnet-4-6" } },
    { "virtualKey": "openai-key", "overrideParams": { "model": "gpt-4o" }, "id": "target-codex" }
  ]
}
```

**Source:** [PortKey Configs Docs (portkey.ai)](https://portkey.ai/docs/product/ai-gateway/configs), [PortKey Conditional Routing (docs1.portkey.ai)](https://docs1.portkey.ai/docs/product/ai-gateway/conditional-routing)

### Key Observation for StackOwl (P3, P6)
StackOwl's `ModelRouter` references the removed `smartRouting` config key — it is inert. The SOTA pattern uses an explicit `model_list` YAML (LiteLLM) or JSON targets array (PortKey) with named model aliases, fallback chains, and routing strategies declared in config — not hardcoded in source code. Winston should replace `smartRouting` with an `IntelligenceConfig` extension that includes `fallbacks[]` and `healthPolicy`.

---

## Section 2 — Cost-Aware Routing

### Market Context (2025–2026)

LLM API prices dropped ~80% between early 2025 and early 2026. Output tokens are 3–8× the cost of input tokens (median 4× ratio). The market moved to tiered routing as the primary cost lever: enterprise benchmarks show routing 70% of queries to budget models, 20% to mid-tier, and 10% to premium reduces average cost 60–80%.

| Model (May 2026) | Input/1M | Output/1M | Best for |
|---|---|---|---|
| DeepSeek V3.2 | $0.14 | $0.28 | Bulk extraction, classification |
| Llama 4 Maverick (hosted) | $0.15 | $0.60 | Mid-quality reasoning |
| GPT-4o-mini | $0.15 | $0.60 | Light code, quick classification |
| Claude Haiku 4.5 | $0.80 | $4.00 | Structured extraction |
| Claude Sonnet 4.6 | $3.00 | $15.00 | Production conversation |
| GPT-4o | $2.50 | $10.00 | Complex code |
| Claude Opus 4.7 | $15.00 | $75.00 | High-stakes synthesis |

**Source:** [LLM API Pricing Comparison 2026 (cloudidr.com)](https://www.cloudidr.com/blog/llm-pricing-comparison-2026), [LLM Cost Per Token Guide 2026 (silicondata.com)](https://www.silicondata.com/blog/llm-cost-per-token), [LLM API Pricing May 2026 (costgoat.com)](https://costgoat.com/compare/llm-api)

### SOTA Cost Routing Patterns

**1. Static tier mapping** (StackOwl's current `IntelligenceRouter`): Task type → tier (high/mid/low) → model. No pricing lookup. Simple but cannot optimize within a tier.

**2. Quality-floor routing** (Martian, OpenRouter): Route to cheapest model that meets a quality threshold score (typically from a small proxy evaluator). Requires per-prompt quality signal.

**3. Budget-cap routing** (PortKey, LiteLLM): Hard daily/monthly spend limits per user/team. Requests beyond limit fail-fast or downgrade to cheaper model. LiteLLM supports `max_budget` per virtual key.

**4. Prompt-caching exploitation**: GPT-5 family offers 90% savings on cached reads; Claude charges 10% of base price for cache hits. For apps with consistent system prompts (StackOwl owl system prompts), cache-hit routing to the same provider saves 70–90% on input tokens. **StackOwl should factor cache hit probability into routing cost estimate.**

### StackOwl Gap (P1, P8)
`costs/pricing.ts:17` has the `MODEL_PRICING` table but it's a hardcoded static source file — not config-driven. `CostTracker` accumulates spend but its output is never fed back into `IntelligenceRouter.resolve()`. The fix is: pass `CostTracker` into `IntelligenceRouter`, add a `resolveCostAware(taskType, budgetRemaining): ResolvedModel` method that checks per-tier cost against remaining daily budget and downgrades tier if over budget.

**Source:** [Understanding LLM Cost Per Token 2026 (silicondata.com)](https://www.silicondata.com/blog/llm-cost-per-token), [LLM Pricing Calculator 2026 (iternal.ai)](https://iternal.ai/calculators/llm-pricing-calculator)

---

## Section 3 — Model Health Monitoring and Failover

### Circuit Breaker Pattern (SOTA 2025–2026)

By mid-2025, 40% of production LLM teams had multi-provider routing with circuit breakers, up from 23% ten months earlier, driven by several major provider outages. The canonical circuit breaker has three states:

```
CLOSED  ──(failure_threshold crossed)──▶  OPEN
  ▲                                          │
  │                                   (recovery_timeout)
  │                                          ▼
  └──(probe success)──────────────  HALF-OPEN
```

**Production thresholds (2025–2026):**
- `failure_threshold`: 5 failures in a sliding 60-second window (balances sensitivity vs false positives)
- `recovery_timeout`: 30 seconds for cloud providers (OpenAI, Anthropic); 10 seconds for local (Ollama)
- Half-open traffic: 10% of normal volume, canary-style with simple validation prompts
- Error rate trigger: 50% in last 100 requests OR last 60 seconds

**Source:** [Circuit Breakers for LLM APIs 2026 (n1n.ai)](https://explore.n1n.ai/blog/circuit-breakers-llm-api-sre-reliability-patterns-2026-02-15), [Circuit Breakers LLM Reliability 2025 (markaicode.com)](https://markaicode.com/circuit-breakers-llm-api-reliability/), [Retries, Fallbacks and Circuit Breakers (portkey.ai)](https://portkey.ai/blog/retries-fallbacks-and-circuit-breakers-in-llm-apps/)

### Passive vs Active Monitoring

| Approach | Latency Cost | Accuracy | Recommended For |
|---|---|---|---|
| **Passive** (detect from live traffic) | Zero | Lags behind actual failures | Always-on layer |
| **Active probes** (synthetic requests on schedule) | Small (1 cheap req/interval) | Detects failures before users hit them | Supplement for critical providers |
| **Combined** (passive primary, active secondary) | Minimal | Best | SOTA production recommendation |

PortKey marks unhealthy targets as "open" continuously from live traffic. Bifrost achieves <11µs overhead for health evaluation inline.

### StackOwl Gap (P2)
`providers/registry.ts:189-195` has a one-shot `healthCheckAll()` probe at startup. Winston should add:
1. A `ProviderCircuitBreaker` class per provider with CLOSED/OPEN/HALF-OPEN state, failure count ring buffer, and recovery timeout
2. Passive recording: every request response goes through `circuitBreaker.recordResult(success, latencyMs)`  
3. An inline check: `circuitBreaker.isOpen()` returns true → skip provider, next in fallback chain
4. Optional active probe: lightweight ping on `recovery_timeout` interval in OPEN state

**Source:** [Top 5 LLM Failover Routing Gateways 2026 (getmaxim.ai)](https://www.getmaxim.ai/articles/top-5-llm-failover-routing-gateways-in-2026/), [LLM Failover High Availability (neuralrouting.io)](https://neuralrouting.io/blog/llm-failover-high-availability-architecture)

---

## Section 4 — Capability-Based Routing

### SOTA Capability Modeling (2025–2026)

Production systems represent model capabilities in three ways:

| Approach | Examples | Pros | Cons |
|---|---|---|---|
| **Static tag arrays** | `capabilities: ["vision", "code", "tool-use"]` | Zero latency | Stale when models update |
| **Benchmark scores** | MMLU, HumanEval, Vision-QA scores | Objective | Requires external evaluation pipeline |
| **Provider self-reported** | OpenAI model cards, Anthropic docs | Current | Provider-biased |

**Most common tags in production 2025–2026:**
- `vision` / `multimodal` — image input capability
- `code` — code generation/completion optimized
- `reasoning` — extended chain-of-thought (o1-style)
- `long-context` — 128K+ context window
- `tool-use` — function calling / structured outputs
- `fast` — optimized for latency over quality
- `structured-output` — JSON mode / schema enforcement

Routing logic: intersection of request-required capabilities with model-declared capabilities → eligible set → apply cost/quality scoring within eligible set.

**Source:** [How to Choose the Right LLM 2026 (llmgateway.io)](https://llmgateway.io/blog/how-to-choose-the-right-llm), [LLM Routing in Production (blog.logrocket.com)](https://blog.logrocket.com/llm-routing-right-model-for-requests/), [IRT-Router: Interpretable Multi-LLM Routing (ACL 2025)](https://aclanthology.org/2025.acl-long.761.pdf)

### StackOwl Gap (P10)
No capability tags exist anywhere in `src/providers/`. The fix is minimal: add `capabilities?: string[]` to `BaseProvider` (or the model config type), populate it in provider constructors or config, and add a `canHandle(required: string[]): boolean` method. Winston's decision: static tags (recommended — zero overhead) vs benchmark-driven (overkill for StackOwl scope).

---

## Section 5 — Model Selection From Task Context

### SOTA Intent-to-Model Routing (2025–2026)

Production systems extract three signal types from each request before model selection:

1. **Domain signals** — medical, legal, code, creative, factual lookup
2. **Complexity signals** — multi-step reasoning required vs simple lookup
3. **Format signals** — structured JSON output needed vs conversational

A 3-step routing pattern is now standard:
1. **Classify** complexity (lightweight 2-shot LLM or small local model, ~5ms)
2. **Select** cheapest model tier meeting quality floor for that complexity
3. **Escalate** if confidence/quality score drops below threshold on response

**Multi-agent context:** Research shows confidence-aware routing that dynamically selects agent roles and model scales based on task complexity (AutoMAS, 2026 Springer). For ReAct loops specifically: tool-heavy tasks → big model; chitchat/clarification → small model; extraction/summarization → cheapest capable model.

**Source:** [Model Routing LLM Best Practices 2026 (abhyashsuchi.in)](https://abhyashsuchi.in/model-routing-llm-2026-best-practices/), [Top 5 LLM Routing Techniques (getmaxim.ai)](https://www.getmaxim.ai/articles/top-5-llm-routing-techniques/), [Multi-Agent LLM Routing 2026 Springer (link.springer.com)](https://link.springer.com/article/10.1007/s43503-026-00088-8)

### StackOwl Gap (P3, P4)
`IntelligenceRouter.resolve(taskType)` already implements step 1 (static classification by task type). The gap is that:
1. `ModelRouter` (which does domain + tool-confidence escalation) reads dead `smartRouting` config
2. `IntelligenceRouter` is `optional` in `gateway/types.ts:346` — it may not be wired

Winston's fix: Make `IntelligenceRouter` required in Gateway construction. Remove `ModelRouter` or merge its still-valid domain-routing logic into `IntelligenceRouter`. Add a `resolveForReAct(taskType, toolCount): ResolvedModel` that escalates tier when `toolCount > N`.

---

## Section 6 — Config Schema Design for Multi-Provider

### Best Practices (2025–2026)

The LiteLLM YAML schema is the de facto standard for open-source multi-provider config. Key design principles:

1. **Model aliases over raw model IDs** — `model_name: "fast"` maps to `"claude-haiku-4-5-20251001"` in litellm_params. Application code uses `"fast"` everywhere; model upgrades are config changes only.

2. **Per-model capability and cost metadata** — production configs include `rpm:`, `tpm:`, `input_cost_per_token:`, `output_cost_per_token:` alongside model identity

3. **Fallback chains as first-class config** — `fallbacks:`, `default_fallbacks:`, `context_window_fallbacks:` declared in router settings, not in application code

4. **Backwards-compat migration**: The SOTA pattern is additive schema extension — add new fields with defaults, deprecate old fields with migration notes in loader. Never rename existing keys without a two-version deprecation window.

### Config Schema for StackOwl (Recommended)

```jsonc
// stackowl.config.json (proposed extension)
{
  "intelligence": {
    "tiers": {
      "high":  { "provider": "anthropic", "model": "claude-sonnet-4-6" },
      "mid":   { "provider": "anthropic", "model": "claude-haiku-4-5-20251001" },
      "low":   { "provider": "anthropic", "model": "claude-haiku-4-5-20251001" }
    },
    "defaults": { "conversation": "mid", "synthesis": "high", "extraction": "low" },
    "fallbacks": [
      { "provider": "openai", "model": "gpt-4o", "forTiers": ["high"] },
      { "provider": "openai", "model": "gpt-4o-mini", "forTiers": ["mid", "low"] }
    ],
    "healthPolicy": {
      "failureThreshold": 5,
      "recoveryTimeoutMs": 30000
    },
    "costPolicy": {
      "maxDailyUsd": 5.0,
      "downgradeTierOnBudgetExhausted": true
    }
  }
}
```

**Backwards-compat note:** `smartRouting` was already removed from `config/loader.ts:408-411`. The new `intelligence.*` fields extend the existing `IntelligenceConfig` type (`intelligence/router.ts:19-23`) without breaking the existing `tiers`/`defaults`/`overrides` fields.

**Source:** [LiteLLM Routing Load Balancing (docs.litellm.ai)](https://docs.litellm.ai/docs/routing), [Multi-Provider LLM Orchestration 2026 Guide (dev.to)](https://dev.to/ash_dubai/multi-provider-llm-orchestration-in-production-a-2026-guide-1g10), [Building Resilient AI Agents Multi-Provider 2026 (stormap.ai)](https://stormap.ai/post/multi-provider-llm-integrations-building-resilient-ai-agents-in-2026)

---

## Section 7 — Hardcoded Model Name Anti-Patterns

### Industry Best Practices (2025–2026)

**The anti-pattern:** Embedding model names like `"gpt-4o"` or `"claude-haiku-4-20250414"` as string literals in source code. When providers deprecate a version, the code silently breaks — or worse, continues calling a deprecated model that returns degraded results.

**SOTA replacement patterns:**

1. **Config aliases with version pinning** (LiteLLM, Datasette LLM):
   ```yaml
   aliases:
     fast: "claude-haiku-4-5-20251001"
     smart: "claude-sonnet-4-6"
   ```
   Application code references `"fast"` — upgrading the model is a config change. ([Model Aliases — LLM Datasette](https://llm.datasette.io/en/stable/aliases.html))

2. **Provider-managed aliases** (OpenAI `gpt-4o-latest`, Anthropic `claude-3-5-sonnet-latest`): Use the provider's rolling alias instead of a specific version string. Keeps you on current model automatically but risks breaking changes.

3. **Auto-discovery from provider API**: Query the provider's model list at startup; select the first model matching capability tags. Latency cost but always current.

**Recommendation for StackOwl:** Config-driven aliases (option 1). The `MODEL_PRICING` table in `costs/pricing.ts` already uses explicit model name strings — adding aliases there or in `IntelligenceConfig` keeps pricing in sync with routing.

### StackOwl Gaps (P5)
- `anthropic-native.ts:385` — `"claude-haiku-4-20250414"` hardcoded as last-resort fallback model name. This is a specific dated version that Anthropic will deprecate. Fix: move to a config default or named constant from `IntelligenceConfig`.
- `protocols/openai.ts:85` — `"gpt-4o"` hardcoded as last-resort default. Fix: same approach — require that the config always specifies a model; throw if missing rather than silently defaulting.

**Source:** [How to Navigate LLM Model Names (developers.redhat.com, Apr 2025)](https://developers.redhat.com/articles/2025/04/03/how-navigate-llm-model-names), [Patterns and Anti-Patterns for Building with LLMs (medium.com)](https://medium.com/marvelous-mlops/patterns-and-anti-patterns-for-building-with-llms-42ea9c2ddc90)

---

## Section 8 — Provider Deduplication / Dead Code

### OpenAI-Compatible Adapter vs Native Protocol (2025–2026)

The OpenAI-compatible API standard is now ubiquitous — vLLM, Together.ai, Groq, local Ollama, and dozens of hosted providers all expose the `/v1/chat/completions` endpoint. This creates a choice in every multi-provider codebase:

| Approach | When to Keep Both | When to Collapse |
|---|---|---|
| **OpenAI-compat + native** | When native API has features absent from OpenAI-compat (extended metadata, streaming differences, Anthropic extended thinking) | When compat covers 100% of your usage |
| **Compat only** | — | When you only use standard `chat/completions` calls |
| **Native only** | When you need provider-specific features exclusively | — |

**2025–2026 industry trend**: Gateway solutions increasingly support multiple native protocols simultaneously (OpenAI + Anthropic + Gemini) rather than collapsing to OpenAI-compat. OpenAI's own guidance (2026): "Reach for a third-party adapter only when the SDK's built-in integration points are not enough." Reason: native SDKs handle streaming format differences, error codes, rate-limit headers, and extended features without translation loss.

**Source:** [OpenAI-Compatible Endpoints LiteLLM (docs.litellm.ai)](https://docs.litellm.ai/docs/providers/openai_compatible), [OpenAI Agents SDK Models (openai.github.io)](https://openai.github.io/openai-agents-python/models/), [Why Your AI App Needs an LLM API Gateway 2026 (ofox.ai)](https://ofox.ai/blog/why-llm-api-gateway-how-to-choose-2026/)

### StackOwl Gap (P7)
`openai-compat.ts` (522 LOC) is not imported in any production code path — it is dead code from before `protocols/openai.ts` (341 LOC) was written. Winston should: verify no runtime import, then delete `openai-compat.ts`. This satisfies the net-file-delta rule (eliminates 522 LOC). StackOwl should rely on `protocols/openai.ts` as the OpenAI protocol implementation, extended if needed for compat-mode providers.

---

## Section 9 — Cost Tracking and Attribution

### SOTA Cost Tracking (2025–2026)

**Per-request attribution** is the baseline: tag every API call with `{userId, sessionId, model, provider}`. From there:

1. **Token counting**: Provider-reported token counts (from response headers/body) are more accurate than pre-request estimation. Tokenization varies by model — same text yields ~20–30% different token count across providers. Always use provider-reported counts when available; estimate as fallback.

2. **Per-user budget enforcement**: Production gateways (Bifrost, PortKey, LiteLLM) enforce budgets at virtual-key level — requests beyond budget are rejected before reaching the provider. StackOwl's `CostTracker` has `maxDailyUsd` / `maxMonthlyUsd` fields but enforcement is post-request (warning only).

3. **EWMA cost tracking**: Per-model rolling average cost helps detect anomalies (prompt injection causing token bloat, model drift).

4. **Unknown model handling**: `costs/pricing.ts:59` returns `undefined` for unknown models → `estimateCost` uses `$0.00`. SOTA: log a warning + use a conservative fallback price (e.g., mid-tier Sonnet pricing) rather than $0 which masks costs.

**Best cost tracking tools 2026:** Braintrust, Helicone, Langfuse, OpenObserve, LiteLLM built-in. All use OpenTelemetry spans with `gen_ai.*` attributes for provider/model/token tagging.

**Source:** [Best LLM Cost Tracking Tools 2026 (getmaxim.ai)](https://www.getmaxim.ai/articles/best-llm-cost-tracking-tools-in-2026/), [From Bills to Budgets LLM Token Usage (traceloop.com)](https://www.traceloop.com/blog/from-bills-to-budgets-how-to-track-llm-token-usage-and-cost-per-user), [LLM Cost Tracking 2026 Braintrust (braintrust.dev)](https://www.braintrust.dev/articles/best-tools-tracking-llm-costs-2026)

### StackOwl Gap (P1, P8)
StackOwl has `CostTracker` + `MODEL_PRICING` but two gaps remain:
1. **Routing feedback**: `CostTracker` data is not read by `IntelligenceRouter` — spend is tracked but never influences model selection.
2. **Unknown model fallback**: `pricing.ts:59` returns `undefined` → $0 cost. Fix: add a `fallbackPrice` (e.g., mid-tier price) used when model not in table; log a warning.
3. **Pre-request budget check**: `CostTracker.canSpend(estimatedCost): boolean` should be called before routing to a tier — if budget exhausted, downgrade tier. Currently enforcement is post-request.

---

## Section 10 — Provider-Level Security and Sandboxing

### API Key Management SOTA (2025–2026)

**The gitignore pattern** (StackOwl's current approach: keys in `stackowl.config.json`, gitignored) is the practical minimum for personal projects. Production patterns in 2025–2026:

| Pattern | Use Case | Pros | Cons |
|---|---|---|---|
| **Env vars** (`ANTHROPIC_API_KEY`) | Dev + CI | Universally supported | Not rotatable at runtime |
| **Config file (gitignored)** | Personal/small team | Simple | No rotation; risk of accidental commit |
| **Secrets manager** (AWS SM, HashiCorp Vault, 1Password Secrets Automation) | Enterprise | Rotation, audit log, scoped access | Infrastructure overhead |
| **Virtual keys** (PortKey, LiteLLM `LITELLM_MASTER_KEY`) | Multi-user/multi-agent | Per-user rate limiting, hide provider keys | Requires gateway |

**Key rotation**: Cloud providers recommend rotation every 90 days. Automated rotation requires either secrets manager integration or provider API support (some providers offer programmatic key generation).

**Virtual keys** are the SOTA multi-agent pattern: the gateway holds real provider keys; each user/agent gets a virtual key with per-key rate limits and budget caps. StackOwl is personal-use so virtual keys are overkill, but per-user spend limits via `CostTracker` are appropriate.

### Rate Limiting (2025–2026)
Shift to token-based rate limiting (tokens per minute, not requests per minute) driven by AI agent traffic growth (Gartner: >30% API demand increase from AI by 2026). Production systems implement:
- **Client-side**: exponential backoff with jitter on 429 responses
- **Gateway-side**: token bucket per provider per virtual key
- **Request queuing**: hold excess requests rather than dropping (PortKey, LiteLLM)

### Container Security Topology (2026)
```
Client → API Gateway (JWT + tiered rate limits)
       → Inference Container (isolated network, non-root, read-only)
       → Model Storage (encrypted-at-rest, read-only bind)
```

**Source:** [LLM API Key Management Rate Limiting (datawiza.com)](https://www.datawiza.com/blog/industry/llm-api-key-management-and-identity-aware-rate-limiting/), [API Rate Limits Best Practices 2026 (orq.ai)](https://orq.ai/blog/api-rate-limit), [Token-Based Rate Limiting AI Agents 2026 (zuplo.com)](https://zuplo.com/learning-center/token-based-rate-limiting-ai-agents), [Local LLM Security Enterprise 2026 (sitepoint.com)](https://www.sitepoint.com/local-llm-security-best-practices-2026/)

### StackOwl Assessment (P8 / security)
StackOwl's gitignore approach is appropriate for its personal-use scope. No changes required for Element 18. If StackOwl grows to multi-user, virtual keys via a gateway would be the upgrade path. The primary security gap is **provider key in plaintext config** — mitigated by gitignore but still a risk if the file is accidentally shared. Recommendation: Add a loader warning if a key looks like it was accidentally committed (check `git log` for `apiKey`). Out of scope for Element 18.

---

## Risk Register R1–R10

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **R1** | Provider landscape shifts: new provider breaks config schema assumptions | Medium | Medium | Use alias-based config with per-model override; adding a provider is additive |
| **R2** | Cost table staleness: model prices change faster than `pricing.ts` is updated | High | Low | Add `updatedAt` timestamp to pricing.ts; log warning if `updatedAt > 90 days` |
| **R3** | Circuit breaker false positives: transient errors trigger OPEN state unnecessarily | Medium | Medium | Use sliding-window failure rate (50% in 60s), not raw count; start threshold at 5 |
| **R4** | Capability tag mismatch: model declared as `vision`-capable but provider API rejects image inputs | Low | High | Test capability tags in integration tests; gate on provider-reported capabilities not just tags |
| **R5** | `IntelligenceRouter` optional field causes silent routing bypass | High | Medium | Make `intelligence` required in Gateway; throw on construction if missing |
| **R6** | Config schema breaking change: new `fallbacks[]` field misread by old loader versions | Low | Medium | Additive extension of existing `IntelligenceConfig`; loader validates and warns on unknown keys |
| **R7** | `openai-compat.ts` deletion breaks an undiscovered import path | Low | Low | Grep all imports before deletion; TypeScript compiler catches remaining references |
| **R8** | Cost routing over-constrains model selection: cheap tier throttled even when budget available | Low | Medium | Only downgrade on budget-exhausted; normal path uses quality-optimized tier |
| **R9** | API key accidentally committed after config refactor | Low | High | Add CI lint rule checking for `apiKey.*:.*sk-` patterns in tracked files |
| **R10** | Model deprecation: hardcoded fallback model names break silently | Medium | Medium | Remove hardcoded names from adapters; require config to specify all model names; throw on missing |

---

## Research Summary: Mapping P1–P10 to SOTA Fixes

| Gap | SOTA Fix Pattern | Section |
|---|---|---|
| P1 — Cost routing disabled | Wire `CostTracker` into `IntelligenceRouter`; add `resolveCostAware()` | §2, §9 |
| P2 — No continuous health monitoring | Add `ProviderCircuitBreaker` class (CLOSED/OPEN/HALF-OPEN) | §3 |
| P3 — Deprecated `smartRouting` config | Replace with `intelligence.fallbacks[]` in extended `IntelligenceConfig` | §1, §6 |
| P4 — `IntelligenceRouter` optional | Make required in Gateway construction | §5 |
| P5 — Hardcoded model names | Config aliases + throw on missing config default | §7 |
| P6 — Provider fixed at startup | Per-request resolution via `IntelligenceRouter` + fallback chain | §1, §5 |
| P7 — `openai-compat.ts` dead code | Verify no imports, then delete | §8 |
| P8 — Pricing table not config-driven | Add `updatedAt`, conservative unknown-model fallback price | §9 |
| P9 — `ModelRouter` dead (dupe of P3) | Remove or merge into `IntelligenceRouter` | §5 |
| P10 — No capability tags | Add `capabilities?: string[]` to provider/model config | §4 |

---

*Research completed 2026-05-10. All section sources cited inline. HALT — awaiting Boss approval before Winston architecture phase.*
