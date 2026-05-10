---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
inputDocuments:
  - _bmad-output/planning-artifacts/element19-skills-engine-audit-2026-05-09.md
workflowType: 'research'
lastStep: 1
research_type: 'market'
research_topic: 'StackOwl Element 19 — Skills Engine'
research_goals: '11-section competitive + academic survey: production skill systems, skill matching/triggering, DAG execution, synthesis from trajectories, quality critique, channel-agnostic UX, invoke_skill as tool, safety/permissioning, always:true semantics, telemetry, naming'
user_name: 'Boss'
date: '2026-05-09'
web_research_enabled: true
source_verification: true
---

# Market Research: StackOwl Element 19 — Skills Engine

## Research Initialization

### Research Understanding Confirmed

**Topic**: StackOwl Element 19 — Skills Engine (match, inject, synthesize, skill quality, channel-agnostic UX)  
**Goals**: 11-section survey of production skill systems, SOTA matching/triggering, DAG execution, trajectory-based synthesis, quality critique, channel-agnostic UX, sub-skill invocation, safety/permissioning, always-on semantics, telemetry, and naming.  
**Research Type**: Market Research  
**Date**: 2026-05-09  
**Input Document**: Phase 1 audit at `_bmad-output/planning-artifacts/element19-skills-engine-audit-2026-05-09.md` (15 gaps G1–G15 confirmed)

### Research Scope

**11 Research Goals:**

1. Production skill systems — schema, discovery, invocation, execution, composition (8–10 systems)
2. Skill matching / triggering — BM25 vs semantic vs LLM, cost-tier escalation, SOTA tiering
3. Structured skill execution (DAG runners) — step args, partial failure, retry, timeout SOTA
4. Skill synthesis from trajectories — auto-generation from tool sequences, quality gating
5. Skill-quality critique — LLM-as-judge vs heuristics, score axes, threshold tuning
6. Channel-agnostic skill UX — multi-step wizards, SkillManagementRouter confirmation
7. invoke_skill as a tool — LLM-driven sub-skill calls, recursion guards, result synthesis
8. Skill safety & permissioning — permissions, jailbreak via crafted SKILL.md, registry trust
9. always: true semantics — token cost, override hierarchy, auto-demotion
10. Skill telemetry — per-skill metrics, feedback into routing, SOTA persistence
11. Naming — "skill" vs "playbook" vs "action" etc., grandma test, naming verdict

**Research Methodology:**

- Current web data with source verification (2025–2026 preferred)
- Multiple independent sources for critical claims
- Comparison tables for sections 1/2/3/6
- Risk register R1–R11 at end (one risk per section)

### Research Status: Scope confirmed, proceeding with detailed research.

---

---

## Section 1: Production Skill Systems

### 1.1 Anthropic Claude Code — SKILL.md / Agent Skills

**Specification origin:** Agent Skills open standard published by Anthropic on December 18, 2025 (agentskills.io). Claude Code extends the base spec with additional platform-specific fields.

**Schema — Frontmatter Fields:**

| Field | Required | Type | Description |
|---|---|---|---|
| `name` | No (recommended) | string | Directory name used as fallback. Max 64 chars, lowercase/hyphens. |
| `description` | Recommended | string | When Claude should invoke the skill. Truncated at 1,536 chars in listing. |
| `when_to_use` | No | string | Additional trigger context, appended to `description`. |
| `argument-hint` | No | string | Autocomplete hint, e.g. `[issue-number]`. |
| `arguments` | No | string or list | Named positional arg names mapping to `$name` substitutions. |
| `disable-model-invocation` | No | bool | `true` = user-only; description hidden from Claude context. |
| `user-invocable` | No | bool | `false` = hidden from `/` menu; Claude-only invocation. |
| `allowed-tools` | No | space-sep string or list | Pre-approved tools active during skill without per-use prompt. |
| `model` | No | string | Model override for this skill's turn. |
| `effort` | No | enum | `low`/`medium`/`high`/`xhigh`/`max`. |
| `context` | No | enum | `fork` = run in isolated subagent. |
| `agent` | No | string | Which subagent type to use when `context: fork`. |
| `hooks` | No | object | Skill-lifecycle hooks. |
| `paths` | No | glob list | Skill auto-activates only when working on matching files. |
| `shell` | No | enum | `bash` (default) or `powershell` for `!` blocks. |

**String substitutions:** `$ARGUMENTS`, `$ARGUMENTS[N]`, `$N`, `$name`, `${CLAUDE_SESSION_ID}`, `${CLAUDE_EFFORT}`, `${CLAUDE_SKILL_DIR}`.

**Dynamic context injection:** `` !`shell command` `` in body executes before Claude sees the content — output replaces the placeholder. Multi-line via ` ```! ` fence.

**Discovery mechanism:**
- Personal: `~/.claude/skills/<name>/SKILL.md`
- Project: `.claude/skills/<name>/SKILL.md`
- Plugin: `<plugin>/skills/<name>/SKILL.md`
- Enterprise: managed settings
- Monorepo: nested `.claude/skills/` in any subdirectory auto-discovered when editing files in that subtree
- Live file-watching via chokidar; changes within a session take effect immediately
- Legacy `.claude/commands/<name>.md` still works identically

**Invocation:** Slash (`/skill-name`) and natural language (Claude reads description and auto-invokes). Controlled by `disable-model-invocation` / `user-invocable` flags.

**Execution model:** LLM-instructed. Skill body injected as a context message. `context: fork` delegates to isolated subagent (separate history, specific agent type). Compaction: up to 5,000 tokens per skill, 25,000 combined budget.

**Composition/chaining:** No explicit depends-on DAG at spec level. Composition via: (a) skills referencing supporting files, (b) `context: fork` subagent, (c) subagents with `skills` preloaded, (d) shell injection calling other scripts.

**Sources:** code.claude.com/docs/en/skills | agentskills.io/specification | paperclipped.de/en/blog/agent-skills-open-standard-interoperability/ *(December 2025–March 2026)*

---

### 1.2 Agent Skills Open Standard (agentskills.io)

**Base spec frontmatter (cross-platform guaranteed fields):**

| Field | Required | Constraint |
|---|---|---|
| `name` | Yes | 1–64 chars, lowercase alphanum + hyphens, must match directory name |
| `description` | Yes | 1–1,024 chars; should include both WHAT and WHEN |
| `license` | No | Short string or filename |
| `compatibility` | No | Max 500 chars; environment requirements |
| `metadata` | No | Arbitrary key-value map |
| `allowed-tools` | No | Space-sep; experimental, varies by platform |

**Progressive disclosure model:**
1. Metadata (~100 tokens): `name` + `description` loaded at startup for all skills
2. Instructions (<5,000 tokens recommended): full body loaded on activation
3. Resources: `scripts/`, `references/`, `assets/` files loaded on demand

**Adoption (March 2026):** 32 platforms including Anthropic (Claude Code), OpenAI (Codex CLI, ChatGPT), Microsoft (VS Code, GitHub Copilot), Google (Gemini CLI), JetBrains (Junie), AWS (Kiro), Block (Goose), Sourcegraph (Amp). Microsoft and OpenAI adopted within 48 hours of December 18, 2025 release.

**Discovery path fragmentation (not standardized):**
- Claude Code: `.claude/skills/`
- OpenAI Codex: `.agents/skills/`
- Google tools: `~/.gemini/antigravity/skills/`

**Sources:** agentskills.io/specification | inference.sh/blog/skills/agent-skills-overview | firecrawl.dev/blog/agent-skills *(December 2025–March 2026)*

---

### 1.3 Anthropic Claude API — Tool Use

**Tool definition fields:**

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Tool identifier |
| `description` | Yes | When Claude should use it — high-leverage field, small wording changes drastically affect invocation rates |
| `input_schema` | Yes | JSON Schema object |
| `strict` | No | `true` enforces exact schema conformance |
| `type` | No | `"custom"` (client-executed) or named server tools |

**Execution model:** Client tools: Claude returns `stop_reason: "tool_use"` blocks; application executes, sends back `tool_result`; loop continues. Server tools (`web_search_20260209`, `code_execution`, `web_fetch`, `tool_search`) run on Anthropic infrastructure.

**Dynamic tool discovery:** `tool_search` server tool allows Claude to search a registry of 50,000+ definitions without loading all upfront.

**Anthropic engineering recommendations:**
- Consolidate related ops into single tools (reduce ambiguity)
- Return only high-signal info; expose `response_format` enum (`detailed`/`concise`) — ~65% token reduction
- Write actionable error messages with examples
- Track tool call runtime, error rate, token consumption per tool

**Sources:** platform.claude.com/docs/en/agents-and-tools/tool-use/overview | anthropic.com/engineering/writing-tools-for-agents *(2025)*

---

### 1.4 OpenAI Agents SDK

**Agent definition fields (Python):**

| Field | Description |
|---|---|
| `name` | Agent identifier |
| `instructions` | System prompt string or callable |
| `tools` | List of function tools / hosted tools |
| `handoffs` | List of agents this agent can transfer to |
| `guardrails` | Input/output validation functions |
| `output_type` | Pydantic model for structured output |

**Handoff schema:** `agent`, `tool_name_override`, `input_type` (Pydantic model for metadata), `on_handoff` callback, `input_filter` (transforms conversation history for receiving agent), `is_enabled`.

**Tool timeout:** `@function_tool(timeout=<seconds>)` per-tool. `timeout_behavior="error_as_result"` (default): model receives timeout message and can recover. `timeout_behavior="raise_exception"`: raises `ToolTimeoutError`.

**Run termination:** `max_turns` parameter; `MaxTurnsExceeded` exception handleable via `error_handlers`.

**Sources:** openai.github.io/openai-agents-python/handoffs/ | openai.github.io/openai-agents-python/running_agents/ *(2025–2026)*

---

### 1.5 ChatGPT Custom GPT Actions

**Schema:** OpenAPI 3.x spec. Key elements: `info.description`, `operationId`, per-operation `summary`/`description`, `parameters`, `requestBody`, `responses`.

**Discovery:** Manual upload of OpenAPI spec via GPT Builder UI, or URL reference. No filesystem scan.

**Invocation:** Natural language only. No `/command` invocation.

**Execution model:** Structured HTTP calls. OpenAI acts as HTTP proxy. No native chaining.

**Status (2026):** Hundreds of thousands of GPTs in GPT Store. Actions are prototyping layer; Agents SDK is the production successor.

**Sources:** developers.openai.com/api/docs/actions/introduction *(2025)*

---

### 1.6 Cursor Rules (.cursor/rules + .cursorrules)

**Frontmatter fields (.mdc format):**

| Field | Effect |
|---|---|
| `description` | Agent evaluates relevance; no description = manual-only |
| `globs` | Comma-separated file patterns; auto-attached when matching files in context |
| `alwaysApply: true` | Forces inclusion in every session |

**Rule types by field combination:**

| Type | Fields | Behavior |
|---|---|---|
| Always Apply | `alwaysApply: true` | Every session |
| Intelligent | `description` only | Agent decides relevance |
| File-scoped | `globs` only | Auto on matching file |
| Manual | None | `@rule-name` mention |

**5-level hierarchy (2026):** Enterprise → Team → Personal → Project → File-scoped. Higher levels override lower.

**Sources:** cursor.com/docs/context/rules | vibecodingacademy.ai/blog/cursor-rules-complete-guide *(2025–2026)*

---

### 1.7 Continue.dev Custom Prompts

**Schema (config.yaml):**
```yaml
prompts:
  - name: check
    description: Check for mistakes in my code
    prompt: |
      Please read the highlighted code...
    invokable: true   # creates /check slash command
```

**Key fields:** `name`, `description`, `prompt`, `invokable` (creates slash command), `uses` (references a Hub-hosted prompt by namespace `org/prompt-name`).

**Discovery:** `config.yaml` in `~/.continue/` or workspace root. Hub prompts fetched from Continue Mission Control.

**Invocation:** Type `/` then select, or `/name` directly. Headless CLI: `cn --prompt <name>`.

**Sources:** docs.continue.dev/customize/deep-dives/prompts *(2025)*

---

### 1.8 Gemini CLI Custom Slash Commands

**Schema (TOML files):**

```toml
description = "Review a pull request"
prompt = """
Review PR {{args}}
Recent commits: !{git log --pretty=format:"%s" -n 5}
"""
```

**Template variables:** `{{args}}` (command arguments), `!{shell cmd}` (executes shell command at invocation time, embeds output).

**Discovery:** `~/.gemini/commands/` (user-scoped) or `.gemini/commands/` (project-scoped). Namespace separator `:` (e.g. `/git:commit` from `git/commit.toml`).

**Sources:** cloud.google.com/blog/topics/developers-practitioners/gemini-cli-custom-slash-commands *(2025)*

---

### 1.9 Voiceflow V4 (2026)

**Architectural shift:** Eliminated intents, entities, and the full NLU layer. Moved from intent-based routing to agentic reasoning.

**Two structural units:**

| Unit | Nature | Use case |
|---|---|---|
| **Playbook** | Autonomous reasoning, open-ended | Multi-path conversations |
| **Workflow** | Visual deterministic DAG | Scripted business logic |

**Invocation:** Agent reasoning (no explicit slash commands). LLM decides which Playbook to invoke. Playbooks call Workflows as tools.

**Sources:** docs.voiceflow.com/docs/agents | moonside.ai/blog/voiceflow-v4-complete-guide *(2025–2026)*

---

### Section 1 Comparison Table

| System | Definition format | Discovery | Invocation | Execution model | Chaining/DAG |
|---|---|---|---|---|---|
| Claude Code SKILL.md | YAML+MD file | Filesystem scan, live watch | Slash + NL auto | LLM-instructed, optional subagent fork | File refs + subagent fork |
| Agent Skills base spec | YAML+MD file | Platform-specific paths | Platform-defined | Platform-defined | File refs |
| Claude API tool use | JSON Schema in API call | Runtime injection | LLM auto-selects | HTTP client-executed or server-executed | Multi-turn loop |
| OpenAI Agents SDK | Python objects | Runtime registration | LLM + handoff tools | Python async loop | Explicit handoff DAG |
| ChatGPT GPT Actions | OpenAPI 3.x spec | Manual upload / URL | NL only | HTTP via OpenAI proxy | None native |
| Cursor .mdc rules | YAML frontmatter + MD | Dir scan | Agent chat, @mention, always | Context injection | @file references |
| Continue.dev prompts | YAML in config.yaml | config.yaml + Hub | Slash + headless CLI | LLM user message | @mentions |
| Gemini CLI commands | TOML file | Dir scan | Slash + NL | LLM + shell injection | Shell composition |
| Voiceflow V4 | Cloud UI | Cloud registry | LLM agent reasoning | LLM (Playbooks) + DAG (Workflows) | Playbook → Workflow calls |

**Key finding for StackOwl:** The Agent Skills open standard (December 2025) is now the de-facto cross-platform format with 32 adopters. StackOwl's SKILL.md format is already compliant with the base spec. The `user-invocable` and `always` fields StackOwl defines mirror production equivalents. Progressive disclosure (description → body → resources) is built into the spec — aligning with StackOwl's `formatForSystemPrompt()` design intent.

**R1 Risk:** Agent Skills spec is only 5 months old (December 2025). Platform extensions diverge (32 platforms, fragmented discovery paths). StackOwl must track spec evolution without over-indexing on any single platform's extensions.

---

## Section 2: Skill Matching / Triggering — SOTA 2025-2026

### 2.1 The Three-Tier Routing Cascade (Production SOTA)

The dominant production pattern as of 2025-2026 is a **confidence-gated three-tier cascade** that escalates only when needed:

| Tier | Method | Latency | Confidence exit |
|---|---|---|---|
| **Tier 1** | Rule-based (regex, keyword, exact match) | 5–20ms | ≥ 0.8 → exit |
| **Tier 2** | Semantic (bi-encoder embedding similarity) | 20–50ms | ≥ 0.8 → exit |
| **Tier 3** | LLM router (full classification) | 500–2,000ms | Always exits here |

**Reported accuracy:** 96% routing accuracy on evaluation sets using the full cascade. Single-method failures: rule-only breaks on novel phrasing; semantic-only struggles with overlapping domains; LLM-only adds unacceptable latency for every turn.

**Critical finding (SRA-Bench, arXiv:2604.24594, Tsinghua University, April 2026):**
- Benchmark: 5,400 instances, 6 domains, 636 gold skills in 26,262-skill corpus (2.4% target density)
- BM25 Recall@1: 57.2% (TheoremQA) to 7.0% (ToolQA) — highly domain-dependent
- Hybrid BM25 + dense: best overall recall
- LLM reranking: most effective across domains
- **Agents are "relevance-unaware" and "need-unaware":** they load skills at nearly identical rates regardless of whether gold skills were retrieved. Retrieval quality alone does not solve skill activation — agents must be explicitly prompted to condition on retrieved skill relevance.
- Oracle vs. no-skill gain: 14–44 percentage point accuracy improvement from correct skill exposure.

**Tool RAG at scale:** Anthropic research shows tool selection accuracy improved from **13% → 43%** when RAG-selected tool subsets replaced full tool enumeration. Prompt length halved.
_(Source: Red Hat, citing Anthropic MCP research, November 2025)_

**SkillsMP scale (April 2026):** 1,000,000+ skills in the SkillsMP marketplace — full enumeration in context is physically impossible.

**Sources:** blog.meganova.ai/the-3-tier-routing-cascade/ | arxiv.org/html/2604.24594 *(April 2026)* | next.redhat.com/2025/11/26/tool-rag-the-next-breakthrough-in-scalable-ai-agents/ *(November 2025)*

---

### 2.2 Cost-Tier Model Routing

**Typical 3-tier cost allocation (production deployments, 2025–2026):**

| Tier | Models | Latency | Cost/req | % of traffic |
|---|---|---|---|---|
| Small | Claude Haiku, GPT-4o-mini | 150ms | $0.001 | ~80% |
| Medium | Claude Sonnet, GPT-4o | 500ms | $0.01 | ~15% |
| Large | Claude Opus, GPT-4 | 1,500ms | $0.05 | ~5% |

**Escalation trigger:** Confidence-based. Small model output probability distribution used as proxy for task difficulty.

**Graph-Based Self-Healing Tool Routing (arXiv:2603.01548, 2026):**
- Treats most tool routing as shortest-path computation (Dijkstra on cost-weighted tool graph), not LLM inference
- Failed tool edges reweighted to infinity → automatic reroute without LLM call
- LLM reserved only for paths with no feasible deterministic route
- Result: **93% fewer control-plane LLM calls** (9 vs. 123) while matching ReAct accuracy

**Sources:** arxiv.org/abs/2603.01548 *(2026)* | zylos.ai/research/2026-02-19-ai-agent-cost-optimization-token-economics *(2026)*

---

### 2.3 Latency Budget Reference

| Stage | Typical range |
|---|---|
| Context retrieval (vector search) | 20–50ms p50 / 50ms p99 |
| LLM inference — Haiku/GPT-4o-mini | 100–400ms |
| LLM inference — Sonnet/GPT-4o | 300–1,500ms |
| Tool execution (internal API) | 10–200ms |
| Tool execution (external API) | 100–2,000ms |

**50ms threshold:** Sub-50ms retrieval becomes negligible vs. LLM inference time.

**Latency budget templates:**

| Mode | Target | Budget breakdown |
|---|---|---|
| Interactive chat | <2,000ms | Context 50ms + LLM 800ms + Tool 200ms + overhead |
| Real-time decision | <500ms | Context 10ms + LLM 200ms |
| Background processing | <30,000ms | Context 200ms + LLM 3,000ms + Tool 5,000ms |

**Source:** streamkap.com/resources-and-guides/agent-decision-latency-budget *(2025)*

---

### 2.4 Hybrid BM25 + Semantic Retrieval Pattern

**Recommended hybrid pattern:**
1. BM25 (sparse) for exact term/API name matching — captures lexical signals
2. Dense bi-encoder (BGE, Contriever) — captures semantic similarity
3. Cross-encoder or LLM reranker on combined candidate set
4. Fusion via Reciprocal Rank Fusion (RRF) or learned linear combination

**Tool-to-Agent Retrieval (arXiv:2511.01854, 2025):** Recall@K improvements up to 27.28 over baseline ToolBench retrievers using hybrid retrieval + usage-driven tool embeddings.

**Key finding for StackOwl:** StackOwl's IntentRouter Tier 1 (BM25) returns a flat `score: 0.5` stub — it is neither BM25 nor semantic. The SOTA recommendation is to either restore real BM25, use dense bi-encoder only, or use a proper hybrid. The flat stub is actually *worse* than no Tier 1 (it promotes all skills equally, then fails to distinguish, wastes time, and still falls through to Tier 3 LLM).

**R2 Risk:** SRA-Bench finding that agents are relevance-unaware means fixing retrieval alone is insufficient — the system prompt must explicitly instruct the model to condition on matched skills. StackOwl's G2 bug (skills never reach the LLM) and this finding are compounding: even after G2 is fixed, an explicit instruction to use the retrieved skill is needed.

**Sources:** arxiv.org/html/2604.24594 *(April 2026)* | arxiv.org/html/2511.01854v1 *(2025)* | arxiv.org/pdf/2603.23013 *(2026)*

---

## Section 3: Structured Skill Execution — DAG Runners

### 3.1 Cross-System Execution Comparison Table

| System | Step args passing | Retry policy | Timeout mechanism | Partial failure | DAG support |
|---|---|---|---|---|---|
| LangGraph | Shared typed state dict | Node-level `retry_policy` | External asyncio wrap (not built-in) | Checkpoint/replay from last checkpoint | State machine graph |
| CrewAI | Context list (task outputs) | `guardrail_max_retries` (default 3) | `max_execution_time` field | Sequential stop; no checkpoint | `context=[]` dependency list |
| OpenAI Agents SDK | Conversation history + RunContext | Per-tool `timeout` + `max_turns` | `@function_tool(timeout=N, timeout_behavior=...)` | MaxTurnsExceeded; tool `error_as_result` | Explicit handoff DAG |
| AutoGen | Chat summaries forwarded | None native | External asyncio | Partial trace not exposed | Group chat round-robin |
| n8n | Node expression `$node[...]` | Per-node retry (3–5 attempts) + exponential backoff | `N8N_RUNNERS_TASK_TIMEOUT` env var | Error Workflow (separate flow) | Visual nodes + edges |
| Voiceflow | Workflow state | Not documented | Not documented | Not documented | Playbook → Workflow |

---

### 3.2 OpenAI Agents SDK — Timeout SOTA (Most Explicit API)

```python
@function_tool(timeout=30.0, timeout_behavior="error_as_result")
async def my_tool(args):
    ...
```

- `error_as_result`: model receives description of timeout, can attempt recovery or alternative
- `raise_exception`: hard fail, stops the run

**Max-turns timeout:**
```python
result = await Runner.run(agent, input, max_turns=50,
    error_handlers={"max_turns": lambda ctx: RunErrorHandlerResult(output="I've hit my limit.")})
```

---

### 3.3 AbortController — SOTA for TypeScript/JavaScript Cancellation

**The problem with `Promise.race()` alone:** Losing promises continue running. A timed-out tool call still consumes resources.

**AbortController actually cancels in-flight operations.**

**SOTA combined pattern:**
```typescript
const timeoutSignal = AbortSignal.timeout(30_000);         // 30s hard limit
const userController = new AbortController();               // user-initiated cancel
const signal = AbortSignal.any([timeoutSignal, userController.signal]);

try {
  const result = await fetch(url, { signal });              // propagates to fetch
  await someAsyncOp({ signal });                            // propagates to any abortable
} catch (err) {
  if (err.name === "TimeoutError") { /* AbortSignal.timeout fired — may retry */ }
  if (err.name === "AbortError")   { /* user cancel — do NOT retry */ }
}
```

**Key APIs:**

| API | Purpose |
|---|---|
| `new AbortController()` | Creates controller + signal pair |
| `controller.abort()` | Manually trigger cancellation |
| `AbortSignal.timeout(ms)` | Auto-fires after ms; no manual clearTimeout needed |
| `AbortSignal.any([sig1, sig2])` | Composite: fires when any input fires |

**Propagation:** Signals chain through call stacks. Pass `{ signal }` down to tool implementations so parent cancellation cancels child operations.

**Key finding for StackOwl G13:** StackOwl's `executor.ts:443-464` uses `Promise.race()` + `setTimeout`. `clearTimeout` only called on success path. The timed-out `fn()` promise continues unsupervised. Fix: replace with `AbortSignal.timeout(ms)` + pass signal to tool implementations. This is a one-file fix.

**Sources:** betterstack.com/community/guides/scaling-nodejs/understanding-abortcontroller/ | kettanaito.com/blog/dont-sleep-on-abort-controller | nearform.com/insights/using-abortsignal-in-node-js/ *(2025–2026)*

---

### 3.4 LangGraph Durable Execution (Checkpoint/Resume)

**Execution model:** Typed state machine. Nodes read/write shared typed state dict. Persistence layer saves state at every node completion.

**Partial failure recovery:**
- On failure: resume from last checkpoint
- Re-executes from checkpoint node (nodes must be idempotent)
- Subgraph resumption: resumes at parent node that called subgraph, then internally at specific node where subgraph stopped

**Known limitations:** No native per-run timeout. Community workaround: external asyncio timeout.

**Sources:** docs.langchain.com/oss/javascript/langgraph/durable-execution *(2025–2026)*

---

### 3.5 CrewAI Task Schema — Key Fields

| Field | Default | Purpose |
|---|---|---|
| `context` | — | Dependencies: task waits for listed tasks' completion; output relayed as input |
| `async_execution` | False | Don't block subsequent tasks |
| `guardrail` / `guardrails` | — | Validation; `(False, error_message)` triggers re-execution |
| `guardrail_max_retries` | 3 | Retry limit on validation failure |
| `human_input` | False | Pause for human review |
| `output_pydantic` / `output_json` | — | Structured output |

**R3 Risk:** LangGraph lacks built-in timeout (known production issues with deployment timeouts in Cloud). n8n's tool node errors fail the entire workflow immediately, bypassing retry configuration — a known unresolved bug (issue #24042, 2025). StackOwl's `SkillExecutor` is ahead of both on timeout ergonomics — just needs the `Promise.race` → AbortController migration.

**Sources:** docs.crewai.com/en/concepts/tasks | github.com/n8n-io/n8n/issues/24042 *(2025–2026)*

---

## Section 4: Skill Synthesis from Trajectories

### 4.1 Voyager — Production Skill Library (Reference Architecture)

Voyager (MineDojo, arXiv:2305.16291) is the canonical production example of continuous skill synthesis from trajectories. The algorithm:

1. **Synthesize**: After each successful task, winning code is stored as a reusable function indexed by a semantic embedding of its natural-language description.
2. **Retrieve**: For new tasks, the top-5 most semantically similar skills are retrieved and injected as context.
3. **Refine**: Up to 4 rounds of iterative refinement per task, using three feedback channels: environment observations, execution errors, and a second GPT-4 self-verification call acting as judge.
4. **Reuse vs. Create**: A new skill is synthesized only when the trajectory has no match in the existing library (cosine similarity < 0.9 threshold).

**Results:** 3.3× more unique items discovered, 2.3× longer exploration distances, 15.3× faster tech-tree unlocks vs. SOTA. This validates continuous synthesis as a driver of emergent capability.

**Sources:** voyager.minedojo.org | arxiv.org/abs/2305.16291 | github.com/minedojo/voyager *(2023; used as 2026 reference architecture)*

---

### 4.2 AgentEvolver / AgentGym — Trajectory-to-Skill Abstraction

AgentEvolver (arXiv:2511.10395, November 2025) introduces three synergistic mechanisms:
- **Self-Questioning**: Generates new task hypotheses autonomously, reducing dependence on handcrafted datasets.
- **Self-Navigating**: Reuses trajectory experience across tasks.
- **Self-Attributing**: Assigns differentiated rewards to trajectory states based on contribution to success — enabling sample-efficient skill extraction.

Training flow: `Environments → Tasks → Trajectories → Agent Policy & Skills`. Skill synthesis is treated as a distillation problem: sequences that appear frequently in high-reward trajectories become parameterized skill procedures.

AgentGym (7 real-world scenarios, 14 environments, 89 benchmark tasks) provides reliable evaluation infrastructure for trajectory-to-skill systems.

**Sources:** arxiv.org/abs/2511.10395 | agentgym.github.io *(2025)*

---

### 4.3 ReST-MCTS* — Process-Reward-Guided Synthesis (NeurIPS 2024)

ReST-MCTS* integrates process reward guidance with Monte Carlo Tree Search:
- Explores solution space via MCTS, collecting reasoning traces annotated with per-step value estimates.
- **Process rewards** (correctness at each intermediate step) are more informative for skill extraction than final outcome alone.
- Avoids manual per-step annotation by inferring correct process rewards from oracle final answers via RL.

A trajectory with correct intermediate steps produces a higher-quality skill candidate than one that only got the final answer right.

**Sources:** arxiv.org/abs/2406.03816 | github.com/THUDM/ReST-MCTS *(NeurIPS 2024)*

---

### 4.4 LangGraph Memory-Augmented Planning (2025–2026)

LangGraph has evolved to support trajectory-based skill synthesis via:
- **Persistent Checkpoints**: State saved at every node completion — trajectories are durable.
- **Procedural Memory**: Synthesized skills encoded as system prompts, tool registries, and function definitions.
- **Multi-Layer Architecture**: Separates working memory (current task) from episodic memory (trajectory history) from procedural memory (synthesized skills).

Frequently-executed sub-task sequences in LangGraph's Planner component become candidates for skill encapsulation.

**Sources:** medium.com/@vinodkrane/next-generation-agentic-rag-with-langgraph-2026 | github.com/langchain-ai/deepagents *(2025–2026)*

---

### 4.5 SkillsBench — The Decisive Quality-Gating Argument

**SkillsBench** (arXiv:2602.12670, 2026) evaluated skill impact across 86 tasks, 11 domains, 7 agent models, 7,308 trajectories:

| Condition | Avg task success delta |
|---|---|
| Curated skills (human-reviewed) | **+16.2 pp** |
| Self-generated skills (auto-synthesized) | **−1.3 pp** |
| Best domain (Healthcare, curated) | +51.9 pp |
| Worst domain (Software Engineering, curated) | +4.5 pp |

**Why self-generated skills fail:** Models confabulate plausible-sounding procedures that don't work. Retrieval quality alone doesn't save them — agents are "need-unaware" and apply skills incorrectly regardless of match quality (SRA-Bench, arXiv:2604.24594, April 2026).

**Key finding for StackOwl (G7):** StackOwl's synthesis loop (CognitiveLoop + ProactivePinger + IdleEngine) is entirely unreachable (G7). This is **the right default**. SkillsBench proves auto-synthesis degrades performance. **Do not re-enable synthesis** without implementing the quality gate described below.

### 4.6 Minimum Quality Gate Before Persistence

Production-grade gating before writing to the skill library:

1. **Frequency threshold**: Sequence appears in ≥5–10% of high-reward trajectories (domain-dependent).
2. **Instruction clarity heuristic**: No hedging language ("try to", "maybe", "attempt"). Required inputs documented. Outputs deterministic.
3. **Deduplication**: Vector similarity against existing library > 0.95 → reject as duplicate.
4. **Sandbox execution test**: Dry-run on 3–5 sample tasks without errors before persistence.
5. **LLM critic gate**: `SkillCritic` score ≥ 0.85 on clarity + trigger precision + completeness axes (per Section 5 architecture).
6. **Human review queue**: Auto-accept only when all gates pass AND skill is not the first of its kind.

**Acceptance formula:** `ACCEPT IF frequency ≥ threshold AND heuristic_pass AND dedup_score > 0.95 AND sandbox_pass AND critic_score ≥ 0.85`

**Sources:** arxiv.org/abs/2602.12670 (SkillsBench, 2026) | arxiv.org/html/2604.24594 (SRA-Bench, April 2026) | skillsbench.ai *(2026)*

**R4 Risk:** SkillsBench −1.3pp finding means any re-enabled synthesis that bypasses quality gates will degrade StackOwl's task success. The minimum bar is: human-reviewed or critic-gated skills only; auto-accept is never safe. Deferred synthesis is not a bug — it's a correct default.

---

## Section 5: Skill-Quality Critique — LLM-as-Judge vs Heuristics

### 5.1 Self-Refine Applied to Skills

Self-Refine (Madaan et al., arXiv:2303.17651, 2023) applied to skill quality:

1. **Generator**: Create candidate skill from trajectory or user spec.
2. **Critic**: Same LLM analyzes for: name clarity, instruction precision, trigger conditions, completeness, security.
3. **Refiner**: Incorporate critique to revise the skill.

Results: ~20% absolute improvement vs. one-shot generation. For skills: clearer instruction text, renamed vague triggers, removed dangerous permissions.

**Sources:** arxiv.org/abs/2303.17651 | github.com/madaan/self-refine *(2023)*

---

### 5.2 Constitutional AI Critique Loop

Constitutional AI (Anthropic, arXiv:2212.08073) operationalizes safety critique:
- **Constitution**: Explicit principles ("Skills should not require admin privileges unless essential").
- **Self-critique**: Model generates critiques against the constitution.
- **Revision**: Model-generated critiques applied iteratively to reduce unsafe instructions.
- **Supervised fine-tuning**: Revised outputs retrain the evaluator.

Applied to skill instructions: a safety constitution checks for privilege escalation, sensitive environment variable access, and destructive operations. Unsafe skills are flagged; if revisable, the loop attempts repair before rejection.

**Sources:** anthropic.com/research/constitutional-ai-harmlessness | arxiv.org/abs/2212.08073 *(2022)*

---

### 5.3 Score Axes for Skill Quality

Production systems score on **5 dimensions** (0–1 scale each):

| Axis | Definition | Bad example | Good example |
|---|---|---|---|
| Name specificity | Descriptive, unambiguous, ≤50 chars | `automation_tool` | `fetch_user_by_email_from_crm` |
| Instruction clarity | Unambiguous, no hedging, testable | "Try to retrieve data if possible" | "Execute API query; return JSON" |
| Trigger precision | Neither too broad nor too narrow | Description matches everything | Description matches a specific task pattern |
| Completeness | All inputs/outputs/errors documented | Missing output schema | Full I/O + error handling documented |
| Safety | No permission escalation or data leaks | Reads `$HOME/.ssh/id_rsa` | Reads only `workspace/` |

**LLM-as-judge calibration (2025 SOTA):** Strong judges (Claude Sonnet, GPT-4o) achieve 80–90% agreement with human evaluators. Calibration protocol: annotate 50–100 skills by human experts → compute Krippendorff's α → iterate until α ≥ 0.80 → document threshold.

**Temperature:** 0.2 for threshold decisions (determinism), 0.8 for critique brainstorming (diversity).

**Sources:** medium.com/@adnanmasood/rubric-based-evals-llm-as-a-judge *(April 2026)* | langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge *(2025)* | arxiv.org/html/2603.00077v2 (AutoRubric, 2026)*

---

### 5.4 Hybrid Architecture: Rule Gate → LLM Judge → Threshold

**SOTA replacement for StackOwl's G9 hardcoded regex approach:**

```
┌──────────────────────────────────────────────────────────┐
│ 1. Rule-Based Gate (Fast Path, ~2ms)                     │
│    • Generic name patterns (via IntelligenceRouter       │
│      cheap-tier, NOT hardcoded regex — G9 fix)           │
│    • Input/output presence validation                    │
│    • Permission audit against allow-list                 │
│    • Missing required fields check                       │
│    • Fail: REJECT immediately                            │
└──────────────────────────────────────────────────────────┘
                       ↓ (pass)
┌──────────────────────────────────────────────────────────┐
│ 2. LLM Judge (Slow Path, async, cached 24h)             │
│    • 5-axis rubric scoring (temperature 0.2)            │
│    • 3-run self-consistency check                        │
│    • Returns {clarity, trigger_precision, completeness,  │
│               safety, name_specificity}                  │
└──────────────────────────────────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────────┐
│ 3. Threshold Decision                                    │
│    • All axes ≥ 0.85 → ACCEPT                           │
│    • Any axis 0.65–0.85 → HUMAN_REVIEW_QUEUE           │
│    • Any axis < 0.65 → REJECT                           │
└──────────────────────────────────────────────────────────┘
```

**G9 fix**: The 6 hardcoded regex/keyword arrays in `critic.ts:53-76`, `pattern-miner.ts:235-238`, `executor.ts:53-64`, `core.ts:1622-1629` should be replaced with `IntelligenceRouter` cheap-tier classification calls (same pattern as Element 17 banned hardcoded keyword arrays).

**Acceptance threshold tuning:** Plot ROC curve on 50-100 annotated skills. Select threshold where Youden index (TPR − FPR) is maximized. Recalibrate quarterly.

**Sources:** claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills | appen.com/llm-as-a-judge-rubric-design | godaddy.com/resources/news/calibrating-scores-of-llm-as-a-judge *(2025–2026)*

**R5 Risk:** StackOwl's SkillCritic with hardcoded regex (G9) will produce false positives and false negatives as skill vocabulary evolves. IntelligenceRouter cheap-tier is the correct replacement — it classifies intent semantically and can be updated without code changes. The hybrid gate architecture ensures fast rejection of obvious failures while using the LLM only for borderline cases.

---

## Section 6: Channel-Agnostic Skill UX

### 6.1 Telegram BotFather Pattern

BotFather maintains per-user wizard state in a database keyed by `{user_id, wizard_id}`. Each turn writes state atomically; abandoned wizards expire in 24 hours. Skills are registered as Telegram slash commands (`/skill_name`). Users `LIST` installed commands via BotFather's `/mybots → bot commands` interface.

Multi-turn wizard state pattern:
```
Turn 1: "install weather skill" → save {installing: true, skill_name: weather}
Turn 2: "Seattle"              → update {location: Seattle}
Turn 3: "Fahrenheit"           → finalize, clear wizard state
```

### 6.2 Slack Workflow Builder — Custom Steps

Slack's custom workflow steps are declared as JSON manifests with inputs and outputs, then registered via `app.function()` in Bolt.js. Discovery: once installed on workspace, the step appears in Workflow Builder's step palette. State persists to Slack's backend indefinitely (no TTL). Skill management is visual — no slash commands needed.

### 6.3 Discord Slash Subcommands

Discord (2025) implements skill management via subcommands:

```
/skill install <name>
/skill list
/skill show <name>
/skill enable <name>
/skill disable <name>
/skill remove <name>
/skill run <name> [args]
```

Multi-turn state via Button Components and Select Menus, stored in bot memory keyed by `{channel_id, user_id, wizard_id}`. Interaction tokens expire in 3 seconds — bots must acknowledge immediately and store state.

### 6.4 Alexa Skill Blueprints (Non-Developer Path)

Alexa Skill Blueprints (`blueprints.amazon.com`) allows non-technical users to fill in a template → generate a skill in seconds. No code. No developer account. This is the "grandma test" champion: skills as fill-in-the-blank customization, not programming.

### 6.5 Channel Parity Comparison Table

| Verb | CLI | Telegram | Slack | Discord | Voice (Alexa) |
|---|---|---|---|---|---|
| **install** | `/skill install <name>` | Wizard multi-turn | Custom app deploy | `/skill install <name>` | Blueprints template |
| **list** | `/skill list` | BotFather command menu | Step palette (visual) | `/skill list` | "Alexa, what skills do I have?" |
| **show** | `/skill show <name>` | `/help <cmd>` | Workflow step preview | `/skill show <name>` | "Tell me about the weather skill" |
| **enable** | `/skill enable <name>` | Always on | Default enabled | `/skill enable <name>` | Blueprint toggle |
| **disable** | `/skill disable <name>` | Remove from /list | Workflow archived | `/skill disable <name>` | Blueprint disabled |
| **remove** | `/skill remove <name>` | BotFather /setcommands | Delete app step | `/skill remove <name>` | Uninstall from app |
| **run** | `/skill run <name> [args]` | `/skill_name args` | Manual workflow trigger | `/skill run <name> [args]` | Voice trigger phrase |

### 6.6 SkillManagementRouter Confirmation

StackOwl already has `OwlManagementRouter` (Element 17 D7) at `src/gateway/commands/owl-router.ts` as a validated pattern. The `SkillManagementRouter` follows the identical shape:

```typescript
SkillManagementRouter.dispatch(verb, args, deps): Promise<string>
// verb ∈ {install, list, show, enable, disable, remove, run}
```

All 7 verbs above map to channel-agnostic handlers. Channel-specific formatting is applied at the adapter layer. The multi-step wizard (install flow) uses `ChannelAdapterV2.ask()` for parameter collection — same pattern as Element 17 D8 owl-creation wizard.

**Key finding for StackOwl:** StackOwl's `wizard.ts` (currently Telegram-flavored inline keyboards, G10) should be migrated to `src/gateway/wizards/skill-creation.ts` using `ChannelAdapterV2.ask()`. Per-userId Map replaces module-scope singleton. This is identical to the Element 17 D8 owl-creation wizard migration.

**Sources:** docs.slack.dev/workflows/workflow-builder | discordpy.readthedocs.io/en/stable/ext/commands | blueprints.amazon.com | microsoft.github.io/botframework-solutions/overview/skills *(2025–2026)*

**R6 Risk:** Without `SkillManagementRouter`, StackOwl's skill management remains CLI-only or Telegram-only (wizard.ts G10). Voice and Slack users cannot manage skills. The `OwlManagementRouter` template makes this a copy-and-adapt operation, not a net-new build — but it must be shipped as part of Element 19 or channel parity is violated on day 1.

---

## Section 7: invoke_skill as a Tool (LLM-Driven Sub-Skill Calls)

### 7.1 Claude Task Tool — Sub-Task Isolation

Claude Code's Task tool (context: fork) spawns isolated subagents:
1. **Spawn**: `Task.spawn({objective, tools, context_limit})` — sub-agent gets its own context window.
2. **Isolation**: Sub-agent has no access to parent's conversation history or memory storage.
3. **Work**: Sub-agent operates independently; all intermediate reasoning stays within its context.
4. **Result**: Sub-agent returns a summary; parent receives only final output.
5. **Context preservation**: Parent context is not inflated by sub-agent's intermediate work.

This is the architectural model for `invoke_skill`: skill execution is isolated, result is returned as a `tool_result` message.

**Sources:** dev.to/bhaidar/the-task-tool-claude-codes-agent-orchestration-system | platform.claude.com/cookbook/tool-use-context-engineering *(2025)*

---

### 7.2 OpenAI Swarm → Agents SDK Handoff

OpenAI Swarm (now succeeded by Agents SDK, March 2025) defined the canonical handoff pattern:

```python
def transfer_to_support():
    return support_agent  # handoff: orchestrator loads new agent, continues conversation

@function_tool(timeout=30.0, timeout_behavior="error_as_result")
async def invoke_skill(skill_name: str, args: dict):
    ...
```

**Handoff vs. Sub-Agent distinction:**
- **Handoff** (Swarm): Sequential — one agent stops, another starts. Useful for workflows.
- **Sub-Agent** (Task tool): Parallel — parent spawns children, waits. Useful for divide-and-conquer.

For `invoke_skill`, the **Tool Result Pattern** applies: skill is called as a tool, result returned as `tool_result`, parent agent continues its ReAct loop. Not agent turn replacement.

**Sources:** github.com/openai/swarm | openai.github.io/openai-agents-python/running_agents *(2025)*

---

### 7.3 Memory Isolation Model

```
Parent context:          Sub-agent context (isolated):
├─ System prompt         ├─ System prompt (inherited or custom)
├─ Conversation history  ├─ Sub-task objective only
├─ All tools             ├─ Allowed tools subset
└─ Working memory        └─ Fresh working memory

                → skill returns tool_result →

Parent context (resumed):
└─ +[tool_result: {success, result, error}]
```

No cross-contamination: sub-skill's intermediate reasoning, failed tool calls, and errors do NOT appear in the parent context.

---

### 7.4 Recursion Guards — Production Patterns

| Guard | Implementation |
|---|---|
| **Depth limit** | `if (currentDepth >= 5) return { error: "Max recursion depth" }` |
| **Token budget** | Subtract estimated tokens per call; abort if remaining < floor |
| **Cycle detection** | Maintain Set of invoked skill IDs per session; abort if cycle detected |
| **Timeout** | AbortSignal.timeout(30_000) per sub-skill call |
| **Manual breakpoint** | User can interrupt via STOP at any recursion level |

**Common failure modes:** infinite recursion (A calls B calls A), tool-args drift (output schema mismatch), context pollution (sub-agent reasoning leaks), silent failure (sub-agent fails without structured error), token bloat (accumulated context across recursion levels).

---

### 7.5 Wiring Recommendation for StackOwl G5

**Current state:** `invoke_skill` registered at `src/index.ts:768` with no executor → `invoke-skill.ts:64-70` returns `NO_EXECUTOR` on every LLM call.

**Fix:** Pass `SkillContextInjector` as executor at `index.ts:768`. Inside `invoke-skill.ts`:

```typescript
// src/tools/invoke-skill.ts
export function createInvokeSkillTool(skillExecutor?: SkillContextInjector) {
  return {
    name: "invoke_skill",
    description: "Execute a registered skill by name with optional arguments",
    execute: async ({ skill_name, args, _context }) => {
      if (!skillExecutor) return toolError("NO_EXECUTOR", "invoke_skill not configured");
      
      // Depth guard
      const depth = (_context?.skillDepth ?? 0);
      if (depth >= 5) return toolError("MAX_DEPTH", "Recursion depth limit reached");
      
      // Cycle detection
      const visited = new Set(_context?.visitedSkills ?? []);
      if (visited.has(skill_name)) return toolError("CYCLE", `Skill cycle: ${skill_name}`);
      
      // Delegate to injector
      const result = await skillExecutor.executeSkill(skill_name, args, {
        ...(_context ?? {}),
        skillDepth: depth + 1,
        visitedSkills: [...visited, skill_name],
      });
      
      return { success: true, result };
    }
  };
}

// src/index.ts:768
toolRegistry.register(createInvokeSkillTool(this.skillInjector));
```

**Sources:** arxiv.org/html/2602.20867v1 (SoK: Agentic Skills, 2026) | arxiv.org/html/2605.06614 (SkillOS, 2026) | livekit.com/blog/react-pattern-voice-agents *(2025)*

**R7 Risk:** `invoke_skill` is currently a silent lie to the LLM — it's in the tool catalog but always returns `NO_EXECUTOR`. This erodes LLM trust and wastes context on a tool that never works. Either wire it (D5 in Winston's architecture) or delete it from the catalog. Leaving it broken is worse than either option.

---

## Section 8: Skill Safety & Permissioning

### 8.1 SkillJect / ToxicSkills Attack Taxonomy (February 2026)

**SkillJect** (February 2026, ResearchGate) demonstrates automated stealthy skill-based prompt injection:

- **Decoupling attack**: Malicious intent is separated from operational payload. The SKILL.md body contains an inducement prompt (appears benign) that persuades the agent to execute an auxiliary script. The actual malicious payload is in `scripts/*.sh` or `scripts/*.py` — hidden from the SKILL.md body and therefore invisible to SKILL.md text scanners.
- **Base64 droppers**: Curl|bash payloads base64-encoded to evade regex-based detection.
- **Deceptive legitimacy**: 91% of confirmed malicious skills contain prompt injection. 76 skills confirmed to contain actual malware payloads.

**Snyk ClawHub Audit** (February 2026):
- 3,984 skills scanned from ClawHub and skills.sh
- **13.4% critical severity** (534 skills)
- **36.82% any vulnerability** (1,467 skills)
- Attack methods: malware distribution, prompt injection, exposed secrets (`$ANTHROPIC_API_KEY`, `$HOME/.ssh`), credential leakage

**Sources:** snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub | agensi.io/learn/toxicskills-clawhavoc-agent-skills-security-crisis-2026 *(February 2026)*

---

### 8.2 OWASP LLM Top 10 (2025) — Skill-Relevant Items

- **LLM01:2025 — Prompt Injection** (Top Priority): Direct injection (user prompts) + Indirect injection (external content including SKILL.md files). Attack success rate: >85% against SOTA defenses when adaptive strategies are employed.
- **LLM03:2025 — Supply Chain Attacks**: A single compromised skill in a dependency chain can compromise the entire agent pipeline.
- **RAG Poisoning**: 5 crafted documents can manipulate RAG responses 90% of the time.

**Sources:** genai.owasp.org/llmrisk/llm01-prompt-injection | arxiv.org/html/2604.03081v1 (Supply-Chain Poisoning, 2026)*

---

### 8.3 Anthropic Claude Skills Security Model (Current State)

Current validation is **minimal**. Official guidance: "treat third-party skills as trusted code — read them before enabling." The only barrier to publishing on ClawHub is a GitHub account at least one week old. There is no built-in cryptographic signing, no sandboxed execution, and no publisher verification.

High-risk indicators to check manually:
- References to `$ANTHROPIC_API_KEY`, `$HOME`, `$USER`, `$SHELL`
- Instructions that append parameters to external URLs
- `Prerequisites` sections with `curl | bash` patterns
- Instructions to run scripts in `scripts/` subdirectories

**Sources:** skywork.ai/blog/ai-agent/claude-skills-security-threat-model-permissions-best-practices-2025 | snyk.io/articles/skill-md-shell-access *(2025–2026)*

---

### 8.4 Post-Installation Payload Delivery Threat

**January 2026 incident (Antiy CERT confirmed):** 1,184 malicious skills found on ClawHub with professional documentation and deceptive names (solana-wallet-tracker, youtube-summarize-pro). Delivery mechanism: skills appeared legitimate at install time, then updated post-publication with malicious code. This bypasses install-time signature checks if signatures are only verified at install.

**Recommendation:** Content hashing at install time + periodic re-verification against stored hash.

**Sources:** labs.cloudsecurityalliance.org (CSA Research Note: Agent Context Poisoning via SKILL.md, May 2026)*

---

### 8.5 Minimum Trust Verification for StackOwl's ClawHub Installer

StackOwl's `src/skills/clawhub.ts:37` installs remote skills. Minimum trust gate before `writeFile`:

1. **Content hash**: SHA-256 of SKILL.md at install time; store hash; verify on each load.
2. **Publisher age**: Reject if publisher GitHub account < 30 days old.
3. **Semantic scanner**: `IntelligenceRouter` cheap-tier classification of SKILL.md body for: env variable references, curl|bash patterns, script execution instructions. Flag for user confirmation if detected.
4. **User consent gate**: Display warning for skills requesting network, environment variable, or file-write permissions. Require explicit `--force` flag or interactive confirmation.
5. **Signed SKILL.md** (roadmap): ECDSA or RSA-PSS signature from publisher; store public key pinned to publisher identity. Verify before install.

**Key finding for StackOwl:** The 13.4% critical severity rate is not anomalous — it's baseline for unvetted ecosystems. Any skill from a remote registry must be treated as hostile code until verified.

**Sources:** snyk.io/blog/toxicskills | arxiv.org/html/2604.03081v1 *(2026)*

**R8 Risk:** StackOwl's ClawHub installer has no trust verification layer. A single install from a compromised skill could exfiltrate API keys, install backdoors, or run arbitrary code. This is a P0 security gap that blocks any public recommendation of ClawHub usage. Fix: implement the 4-point gate above before any ClawHub marketing.

---

## Section 9: Skill `always: true` Semantics — Token Cost, Override Hierarchy

### 9.1 Agent Skills Open Standard — No `always` Field

The Agent Skills open standard (**agentskills.io**, December 2025) does **not define an `always` or `alwaysApply` field**. This is a platform-specific extension. The standard's substitute is **progressive disclosure**: metadata (name + description, ~80 tokens per skill) is loaded for all skills at startup; full body is loaded only on semantic activation. This achieves the "always available" benefit at near-zero token cost.

**StackOwl's `always: true` field** (types.ts:27) predates the open standard and has no equivalent in the spec. The design intent is sound; the implementation is absent (G4).

**Sources:** agentskills.io/specification | newsletter.swirlai.com/p/agent-skills-progressive-disclosure *(2025)*

---

### 9.2 Cursor `alwaysApply: true` — Token Cost Warning

Cursor's `.cursor/rules` with `alwaysApply: true` causes:
- ~3,000 tokens consumed before any user input
- 2,000+ extra tokens per message with 20 global always-apply rules
- 25% context window consumed by rules → 25% less space for code

Community guidance (Cursor Forums, 2026): "Stop the token bleeding — use glob-scoped rules instead of alwaysApply." Progressive disclosure is the endorsed alternative.

**Sources:** forum.cursor.com/t/stop-the-token-bleeding *(2026)* | greenido.dev/2025/11/17/8-top-tips-to-actually-use-cursor *(2025)*

---

### 9.3 Progressive Disclosure as the Architecture

**3-tier loading (Agent Skills SOTA — 96.3% token savings vs. full-body loading):**

| Tier | Content | When loaded | Token cost |
|---|---|---|---|
| Level 1 | name + description | Every session, all skills | ~80 tokens/skill |
| Level 2 | Full SKILL.md body (≤5,000 tokens) | On semantic activation | 200–1,000 tokens/invocation |
| Level 3 | Scripts, assets, references | During execution | On-demand |

**Task completion accuracy:** 76% → 91% with progressive disclosure. Refusal rate reduction: 60%. Supports 1,000+ simultaneous skills.

---

### 9.4 Honoring `always: true` in StackOwl (G4 Fix)

**Recommended approach:** Honor `always: true` by pre-pending always-skills descriptions to Level 1 catalog AND pre-loading their full bodies unconditionally (not matched by IntentRouter — injected directly). But gate this with a token budget cap.

```typescript
// In injector.ts — G4 fix site
const alwaysSkills = registry.getByFlag("always");
const matchedSkills = await intentRouter.route(userMessage, eligibleSkills);

// Pre-prepend always skills, then append matched skills (no duplicates)
const toInject = [
  ...alwaysSkills,
  ...matchedSkills.filter(s => !alwaysSkills.includes(s))
];
```

**Auto-demotion pattern (CI-8 from master plan):** If `always` skill EWMA success rate < 70% over 50 invocations, auto-demote to context-injected only. Log demotion to `skill_usage` table.

**Override hierarchy:** `always` skills are base context; context-matched skills are higher priority (more specific overrides more general). Most recent user instruction overrides system defaults.

**Sources:** skills.deeptoai.com/en/docs/development/progressive-disclosure-architecture | codewithseb.com/blog/claude-code-skills-reusable-ai-workflows-guide *(2025)*

**R9 Risk:** Implementing `always: true` as flat always-inclusion without progressive disclosure architecture replicates the Cursor token-bleeding problem. With 10 always-skills × 500 tokens/body = 5,000 tokens wasted per turn on context that may be irrelevant. The auto-demotion pattern (CI-8) is the production-grade solution — wire it alongside the G4 fix.

---

## Section 10: Skill Telemetry — Per-Skill Metrics, Feedback into Routing

### 10.1 What Production Systems Track

**LangSmith (2025):** Per-tool/skill: token usage (input/output/cost), latency (P50/P99), error rate, tool selection accuracy, user feedback scores. Nested span tracing: each tool invocation is a traced span with parent-child relationships.

**Langfuse SDK v4 (OTEL-native, 2025–2026):** OpenTelemetry as the standard for LLM observability. Captures: prompt sent, response received, model used, token counts, retrieval latency (if RAG), user corrections, manual quality review scores.

**Performance benchmarks (2025–2026):**
- Enterprise agent target: 85–95% autonomous completion for structured tasks
- Goal accuracy below 80% → immediate action
- False positive rate target: < 2%
- Voice response: < 800ms for natural experience

**Sources:** langchain.com/langsmith/observability | langfuse.com/integrations/native/opentelemetry | masterofcode.com/blog/ai-agent-evaluation *(2025–2026)*

---

### 10.2 EWMA for Routing Re-ranking

Exponentially Weighted Moving Average for success rate:

```
EWMA(t) = α × success(t) + (1 − α) × EWMA(t−1)
```

Recommended α = 0.3 for skill routing (recent data 30% weighted, history 70%). Balances stability with responsiveness. Used to re-rank skills for semantic matching: same query → higher-success skills surface first.

**Feedback loop:**
- Track user corrections as negative feedback (user restates same task → implicit correction signal)
- If `correction_rate > 5%` → reduce probability of selecting that skill for similar tasks
- Auto-demote always-skills with EWMA < 70% (see Section 9.4)

---

### 10.3 Decay Policies

| Trigger | Policy |
|---|---|
| Skill SKILL.md updated | Decay historical metrics by 50% (old performance less relevant) |
| Time-based | Exponential decay: metrics > 30 days old weighted 50% vs. recent |
| Sample size too small | If invocations < 10, use prior (default success rate) not EWMA |
| Admin reset | Manual reset for A/B testing new skill versions |

---

### 10.4 SQLite Schema for StackOwl (G11 Fix)

**Recommended `skill_usage` table** (replaces `workspace/skills-stats.json`):

```sql
CREATE TABLE skill_usage (
  id INTEGER PRIMARY KEY,
  skill_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  triggered_by TEXT,          -- 'user_command', 'semantic_match', 'always_included'
  timestamp INTEGER NOT NULL,
  latency_ms REAL,
  tokens_in INTEGER,
  tokens_out INTEGER,
  success BOOLEAN,
  user_correction BOOLEAN,
  user_rating INTEGER,        -- 1–5 scale
  FOREIGN KEY (skill_id) REFERENCES skills(id)
);
CREATE INDEX idx_skill_timestamp ON skill_usage (skill_id, timestamp);

CREATE TABLE skill_aggregate_stats (
  skill_id TEXT PRIMARY KEY,
  total_invocations INTEGER DEFAULT 0,
  success_count INTEGER DEFAULT 0,
  avg_latency_ms REAL,
  ewma_success_rate REAL DEFAULT 0.5,
  user_correction_rate REAL DEFAULT 0.0,
  last_updated INTEGER,
  FOREIGN KEY (skill_id) REFERENCES skills(id)
);
```

**SQLite configuration:** WAL mode (`PRAGMA journal_mode = WAL`), `PRAGMA synchronous = NORMAL`, `PRAGMA busy_timeout = 5000`.

**Cloudflare Agents SDK (2025):** Each Agent instance gets its own SQLite database running in the same context — effectively zero-latency state access. This validates the SQLite-per-agent pattern for edge agents like StackOwl.

**Sources:** developers.cloudflare.com/agents/api-reference/store-and-sync-state | sparkco.ai/blog/advanced-langsmith-tracing-techniques-in-2025 | nerdleveltech.com/sqlite-in-2025 *(2025–2026)*

**R10 Risk:** StackOwl's `skills-stats.json` (G11) is unscalable — JSON parsing becomes a bottleneck at 100+ skills × 1,000+ sessions. It also has no atomic write guarantee (process crash mid-write corrupts the file). SQLite WAL is the proven fix. This is a mechanical migration, not an architectural redesign.

---

## Section 11: Naming Research — "Skill" vs. "Playbook" vs. "Action"

### 11.1 Production System Terminology (2025–2026)

| System | Term | Audience | Notes |
|---|---|---|---|
| Claude Code + Agent Skills standard | **Skill** | Dev + end-user | Official open standard Dec 2025 |
| Agent Skills (agentskills.io) | **Skill** | Cross-platform | 32 adopters |
| OpenAI Agents SDK | **Skill** (adopted) | Dev | Originally "GPT Actions" — aligned post-standard |
| Voiceflow V4 | **Playbook** (autonomous) + **Workflow** (deterministic) | Non-technical operators | Domain-specific split |
| Cursor | **Rule** | Dev | `.cursor/rules` — scope-specific |
| Continue.dev | **Prompt** | Dev | Custom commands |
| Gemini CLI | **Command** | Dev + user | Direct invocation |
| GitHub Copilot | **Skill** | Dev | Adopted standard |
| Alexa | **Skill** | General public | 600M devices, 130,000+ skills |
| LangGraph | **Subgraph** (tech) / **Workflow** (user) | Dev | Modular component |

---

### 11.2 Non-Tech Grandma Test

**Word comprehension without explanation (estimated):**

| Term | Comprehension | Notes |
|---|---|---|
| **Skill** | 80% | "Teach your assistant a new skill" — intuitive |
| Recipe | 70% | "Like a cooking recipe" — approachable but not standard |
| Playbook | 40% | Requires sports/business context |
| Action | 30% | Ambiguous — button? command? verb? |
| Command | 20% | Technical, imperative tone |
| Flow | 15% | Vague, requires explanation |

**Alexa effect:** Alexa has normalized "skill" for 600M device households. Non-technical users already understand "Alexa skills" as "things Alexa can do for me." StackOwl can inherit this recognition for free.

---

### 11.3 Agent Skills Open Standard Adoption

December 18, 2025: Anthropic releases Agent Skills as an open specification. Within 48 hours, Microsoft and OpenAI adopt. Current: **32 platforms** including Claude, OpenAI Codex, Gemini CLI, GitHub Copilot, VS Code, Cursor, JetBrains, AWS Kiro, Block Goose, Sourcegraph Amp. Using any other term creates friction with this ecosystem.

---

### 11.4 Element 17 Naming Precedent — "Helper"

Element 17 chose **"Helper"** for owls. The parallel naming logic:
- **Helper** = the agent/assistant (what the owl *is*)
- **Skill** = the capability/extension (what the Helper *can do*)

These are orthogonal terms — "Helper Skill" as the compound is coherent and reinforces the relationship: "This is a skill your Helper can use."

---

### 11.5 Naming Verdict

**Verdict: Keep "Skill" as the canonical technical term. Use "Helper Skill" in user-facing surfaces.**

| Context | Term |
|---|---|
| SKILL.md file format | `skill` |
| Slash commands | `/skill install`, `/skill list` |
| API/types.ts | `Skill`, `SkillMetadata` |
| User-facing UI text | "Helper Skills" or "Skills" |
| Documentation | "Skills" (primary) with "what your Helper can do" as explanatory text |

**Why not "Playbook":** Enterprise document connotation; Voiceflow uses it differently. **Why not "Action":** GPT Actions is being superseded by the Skills standard. **Why not "Command":** Technical jargon, not approachable.

**Sources:** agentskills.io/home | grabon.com/blog/alexa-statistics | mindstudio.ai/blog/agent-skills-open-standard *(2025–2026)*

**R11 Risk:** Deviating from "skill" to "action", "playbook", or "command" fragments the ecosystem, confuses users familiar with Claude Code Skills or Alexa, and breaks composability with the Agent Skills open standard. The only valid case for deviation is if Anthropic pivots the term (no signs of that as of May 2026). Keep "skill."

---

## Risk Register R1–R11

| Risk ID | Section | Risk Statement | Severity | Mitigation in Architecture |
|---|---|---|---|---|
| **R1** | Production skill systems | Agent Skills spec is only 5 months old (Dec 2025); 32-platform fragmentation of discovery paths and extensions. StackOwl may track the wrong platform's extensions. | Medium | Track `agentskills.io/specification` as canonical source. Implement only base-spec fields. Platform extensions (Claude Code `paths`, `model`, `effort`) as optional. |
| **R2** | Skill matching / triggering | SRA-Bench: even with perfect retrieval, agents are "relevance-unaware" — they invoke skills at nearly identical rates regardless of gold-skill retrieval. Fixing G2 (wiring injection) is necessary but not sufficient. | High | After G2 fix, add explicit system-prompt instruction: "Use the Skills listed in ## Skills if they apply to the current request." |
| **R3** | Structured skill execution | LangGraph lacks built-in timeout; n8n tool errors bypass retry config (issue #24042). StackOwl's SkillExecutor is ahead of both — but G13 (`Promise.race` leak) means in-flight tools run unsupervised after timeout. | Medium | Replace `Promise.race` + `setTimeout` with `AbortSignal.timeout(ms)` + pass signal to tool implementations. One-file fix in `executor.ts:443-464`. |
| **R4** | Skill synthesis from trajectories | SkillsBench: self-generated skills −1.3pp vs. no-skills baseline. Any re-enabled synthesis without quality gating will degrade StackOwl's performance. | High | G7 synthesis loop disabled is the CORRECT default. Do not re-enable without: heuristic gate + LLM critic gate + sandbox test + 5% improvement threshold. |
| **R5** | Skill-quality critique | StackOwl's hardcoded regex (G9) in `critic.ts` produces false positives/negatives as skill vocabulary evolves; 6 violation sites. | High | Replace all G9 sites with `IntelligenceRouter` cheap-tier classification. Adopt hybrid rule-gate → LLM-judge architecture (Section 5.4). |
| **R6** | Channel-agnostic skill UX | Without `SkillManagementRouter`, skill management is CLI-only or Telegram-only (wizard.ts is Telegram-flavored inline keyboards). | High | Implement `SkillManagementRouter` (D8) using `OwlManagementRouter` as template. Migrate wizard to `src/gateway/wizards/skill-creation.ts` with `ChannelAdapterV2.ask()`. |
| **R7** | invoke_skill as a tool | `invoke_skill` is in the LLM's tool catalog but always returns `NO_EXECUTOR`. LLM attempts to call it, always fails silently, eroding trust and wasting context. | High | Wire `SkillContextInjector` as executor at `index.ts:768`. Add recursion guard (depth 5, cycle detection, 30s timeout). Or delete the tool from the catalog — either is better than current state. |
| **R8** | Skill safety & permissioning | ClawHub: 13.4% critical severity, 36.82% any vulnerability. SkillJect technique bypasses SKILL.md text scanners by hiding payloads in auxiliary scripts. | Critical | Implement 4-point trust gate in `clawhub.ts:37`: content hash, publisher age check, semantic scanner (via `IntelligenceRouter`), explicit user consent for high-risk permissions. |
| **R9** | `always: true` semantics | Implementing `always: true` as flat always-inclusion replicates Cursor token-bleeding (3,000+ tokens per turn wasted on irrelevant skill bodies). | Medium | Honor `always` by pre-pending bodies, but gate with token budget cap. Adopt progressive disclosure (Level 1 descriptions for all, Level 2 on demand). Add auto-demotion when EWMA < 70%. |
| **R10** | Skill telemetry | `skills-stats.json` cannot handle 100+ skills × 1,000+ sessions efficiently; no atomic write guarantee; no index for re-ranking queries. | Medium | Migrate to SQLite WAL mode with `skill_usage` + `skill_aggregate_stats` tables. EWMA re-ranking runs periodically. One-file migration in `tracker.ts`. |
| **R11** | Naming | Using "action", "playbook", or "command" instead of "skill" breaks composability with 32-platform Agent Skills ecosystem and confuses users familiar with Claude Code Skills or Alexa. | Low | Keep "skill" as canonical term. Use "Helper Skill" in user-facing surfaces to tie to Element 17 metaphor. |

---

*Research completed 2026-05-09. All sources 2025–2026 unless otherwise noted. Phase 3 (Winston architecture review) inherits this risk register and the 15-gap Phase 1 audit.*
