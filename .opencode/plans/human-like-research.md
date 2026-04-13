# StackOwl Human-Like Research System — Implementation Plan

## Overview

This plan adds three core capabilities to StackOwl:

1. **Auto-detect deep research** from message content — no prefix needed
2. **Dynamic iteration with self-check** — model assesses its own progress every N tool calls
3. **Pre-inject pellets + continuity → context** so follow-ups truly understand what was discussed

---

## Files TO BE MODIFIED (7 files)

| Step | File                               | What Changes                                              |
| ---- | ---------------------------------- | --------------------------------------------------------- |
| 1    | `src/orchestrator/types.ts`        | Add `depth` + `researchSignal` to `TaskStrategy`          |
| 2    | `src/config/loader.ts`             | Add `research{}` config section                           |
| 3    | `src/orchestrator/classifier.ts`   | Add `detectResearchIntent()` + wire into classifier       |
| 4    | `src/engine/runtime.ts`            | Self-check every N iterations + `[DEEPER]` marker support |
| 5    | `src/orchestrator/orchestrator.ts` | Add `executeDeepResearch()` + wire depth into execution   |
| 6    | `src/engine/planner.ts`            | Add `createDeepResearchPlan()`                            |
| 7    | `src/gateway/core.ts`              | Wire `ContinuityResult` → context builder                 |

---

## STEP 1: `src/orchestrator/types.ts`

**Goal**: Add depth mode and research signal to the strategy type system.

### Find this section (~line 34):

```typescript
export interface TaskStrategy {
  strategy: StrategyType;
  /** LLM's explanation of why this strategy was chosen */
  reasoning: string;
  /** 0-1 confidence score */
  confidence: number;
  owlAssignments: OwlAssignment[];
  /** Subtasks for PLANNED and SWARM strategies */
  subtasks?: SubTask[];
  /** Parliament-specific config */
  parliamentConfig?: {
    topic: string;
    owlCount: number;
  };
}
```

**Replace with**:

```typescript
export interface ResearchSignal {
  /** Why deep research mode was triggered */
  reason: string;
  /** Subtopics identified in the research query */
  subtopics: string[];
  /** Whether this was auto-detected vs user-explicit */
  autoDetected: boolean;
}

export interface TaskStrategy {
  strategy: StrategyType;
  /** LLM's explanation of why this strategy was chosen */
  reasoning: string;
  /** 0-1 confidence score */
  confidence: number;
  /** Depth mode: "quick" (default) or "deep" (multi-iteration research) */
  depth: "quick" | "deep";
  /** Research signal — present when depth="deep" */
  researchSignal?: ResearchSignal;
  owlAssignments: OwlAssignment[];
  /** Subtasks for PLANNED and SWARM strategies */
  subtasks?: SubTask[];
  /** Parliament-specific config */
  parliamentConfig?: {
    topic: string;
    owlCount: number;
  };
}
```

---

## STEP 2: `src/config/loader.ts`

**Goal**: Add `research{}` config section with self-check and iteration settings.

### Find the existing config interface (search for `interface StackOwlConfig` or `export interface` around config-related types):

Add these new interfaces **after** the existing config interfaces:

```typescript
export interface ResearchConfig {
  /** Auto-detect deep research from message content. Default: true */
  autoDeep: boolean;
  /** Self-check interval (tool call count between self-assessments). Default: 5 */
  selfCheckInterval: number;
  /** Max iterations for deep research tasks (soft cap). Default: 40 */
  maxIterations: number;
  /** Enable diminishing returns detection. Default: true */
  enableDiminishingReturns: boolean;
  /** String similarity threshold for diminishing returns (0-1). Default: 0.7 */
  similarityThreshold: number;
  /** Switch to cloud provider after N consecutive failures. Default: 2 */
  cloudFallbackAfter: number;
}
```

### Find where default values are set (search for `DEFAULT_CONFIG` or a defaults object):

Add `research` to the defaults:

```typescript
research: {
  autoDeep: true,
  selfCheckInterval: 5,
  maxIterations: 40,
  enableDiminishingReturns: true,
  similarityThreshold: 0.7,
  cloudFallbackAfter: 2,
},
```

### Find the `StackOwlConfig` interface and add:

```typescript
  /** Research behavior config */
  research?: Partial<ResearchConfig>;
```

---

## STEP 3: `src/orchestrator/classifier.ts`

**Goal**: Add research intent auto-detection. The classifier already exists — we extend it.

### Add this function BEFORE the `classifyStrategy` function (~line 66):

```typescript
// ─── Research Intent Detection ─────────────────────────────────

const RESEARCH_PATTERNS: Array<{ pattern: RegExp; label: string }> = [
  // Explicit research verbs
  {
    pattern:
      /\b(do research|research|investigate|deep.?(?:search|dive|look)|look into)\b/i,
    label: "research-verb",
  },
  // Comparison queries
  {
    pattern: /\bcompare\s+[^?]+\s+(?:vs|versus|against|and)\s+[^?]+/i,
    label: "comparison",
  },
  // Multi-part / thorough queries
  {
    pattern:
      /\b(tell me everything|explain in depth|comprehensive|thorough|complete picture|full analysis)\b/i,
    label: "thorough",
  },
  // Multi-question (3+ ?)
  { pattern: /\?.*\?.*\?/s, label: "multi-question" },
  // Long research questions (50+ words with multiple keywords)
  { pattern: /^(.{200,})$/s, label: "long-research" },
  // "How do I..." with multiple sub-questions
  { pattern: /\b(how\s+do\s+(?:i|we|they))\b.*\?/i, label: "how-to-deep" },
  // "Everything about" queries
  { pattern: /\beverything\s+about\b/i, label: "everything-about" },
];

const RESEARCH_KEYWORD_COUNT = 3;
const RESEARCH_LONG_THRESHOLD = 50;

function detectResearchIntent(text: string): {
  isDeep: boolean;
  reason: string;
  subtopics: string[];
} {
  const trimmed = text.trim();
  const matchedLabels: string[] = [];

  for (const { pattern, label } of RESEARCH_PATTERNS) {
    if (pattern.test(trimmed)) {
      matchedLabels.push(label);
    }
  }

  // Count research-action keywords
  const researchKeywords = trimmed.match(
    /\b(search|find|lookup|check|analyze|compare|investigate|research|explore|review|evaluate|assess|examine|look up|gather|collect)\b/gi,
  );
  const keywordCount = researchKeywords ? researchKeywords.length : 0;

  // Word count check
  const wordCount = trimmed.split(/\s+/).length;

  // Extract subtopics: split on "and", ",", ";" that are near research content
  const subtopicSplit = trimmed
    .split(/\s*(?:,|;|\band\b|\bvs\.?\b|\bversus\b)\s*/)
    .map((s) => s.trim())
    .filter((s) => s.length > 10 && s.length < 100);

  // Multi-subtopic detection: 3+ distinct comma/and-separated concepts
  const hasMultiSubtopics = subtopicSplit.length >= 3;

  // Decision logic
  if (
    matchedLabels.includes("research-verb") ||
    matchedLabels.includes("comparison")
  ) {
    return {
      isDeep: true,
      reason: matchedLabels.includes("comparison")
        ? "comparison query detected"
        : "explicit research request",
      subtopics: subtopicSplit.slice(0, 5),
    };
  }

  if (
    matchedLabels.includes("thorough") ||
    matchedLabels.includes("everything-about")
  ) {
    return {
      isDeep: true,
      reason: "thorough/comprehensive request",
      subtopics: subtopicSplit.slice(0, 5),
    };
  }

  if (matchedLabels.includes("multi-question") && wordCount >= 30) {
    return {
      isDeep: true,
      reason: `multi-question research (${matchedLabels.join(", ")})`,
      subtopics: subtopicSplit.slice(0, 5),
    };
  }

  if (
    matchedLabels.includes("long-research") &&
    keywordCount >= RESEARCH_KEYWORD_COUNT
  ) {
    return {
      isDeep: true,
      reason: `long research query (${wordCount} words, ${keywordCount} research keywords)`,
      subtopics: subtopicSplit.slice(0, 5),
    };
  }

  if (hasMultiSubtopics && keywordCount >= 2 && wordCount >= 40) {
    return {
      isDeep: true,
      reason: `multi-subtopic research (${subtopicSplit.length} distinct aspects)`,
      subtopics: subtopicSplit.slice(0, 5),
    };
  }

  return { isDeep: false, reason: "", subtopics: [] };
}
```

### Modify the `classifyStrategy` function output

Find where the function returns the parsed result (around line 149 — after JSON parsing):

Add `depth` and `researchSignal` to the returned object:

```typescript
const researchSignal = detectResearchIntent(userMessage);

const result: TaskStrategy = {
  strategy: parsed.strategy ?? "STANDARD",
  reasoning: parsed.reasoning ?? "Default strategy",
  confidence: parsed.confidence ?? 0.5,
  depth: researchSignal.isDeep ? "deep" : "quick",
  researchSignal: researchSignal.isDeep
    ? {
        reason: researchSignal.reason,
        subtopics: researchSignal.subtopics,
        autoDetected: true,
      }
    : undefined,
  owlAssignments: parsed.owlAssignments ?? [
    { owlName: defaultOwl, role: "lead", reasoning: "Default" },
  ],
  subtasks: parsed.subtasks,
  parliamentConfig: parsed.parliamentConfig,
};
```

Also update the `makeDefault` and `makeDirect` helper functions to include `depth: "quick"`:

```typescript
function makeDefault(owlName: string): TaskStrategy {
  return {
    strategy: "STANDARD",
    reasoning: "Default strategy",
    confidence: 0.5,
    depth: "quick",
    owlAssignments: [{ owlName, role: "lead", reasoning: "Default owl" }],
  };
}

function makeDirect(owlName: string): TaskStrategy {
  return {
    strategy: "DIRECT",
    reasoning: "Trivial message, no tools needed",
    confidence: 1.0,
    depth: "quick",
    owlAssignments: [{ owlName, role: "lead", reasoning: "Default owl" }],
  };
}
```

---

## STEP 4: `src/engine/runtime.ts`

**Goal**: Add self-check every N iterations + `[DEEPER]` marker support.

### Find the constants section (search for `MAX_TOOL_ITERATIONS` or `DEFAULT_MAX`):

Change:

```typescript
const DEFAULT_MAX_TOOL_ITERATIONS = 15;
```

To:

```typescript
const DEFAULT_MAX_TOOL_ITERATIONS = 15;
const DEFAULT_DEEP_MAX_TOOL_ITERATIONS = 40;
```

### Add these new functions AFTER the imports section and BEFORE the `OwlEngine` class:

```typescript
// ─── Self-Assessment Engine ───────────────────────────────────

type SelfCheckVerdict = "CONTINUE" | "PIVOT" | "SYNTHESIZE";

interface SelfCheckInput {
  lastToolName: string;
  lastToolResult: string;
  recentToolResults: string[];
  userMessage: string;
  iterationsUsed: number;
  maxIterations: number;
  similarityThreshold: number;
}

function shouldSkipSelfCheck(iterations: number, interval: number): boolean {
  return iterations === 0 || (iterations + 1) % interval !== 0;
}

function detectDiminishingReturns(
  results: string[],
  threshold: number,
): boolean {
  if (results.length < 3) return false;
  const last3 = results.slice(-3);
  const sim = (a: string, b: string) => {
    const wordsA = new Set(a.match(/\b[a-z]{3,}\b/gi) ?? []);
    const wordsB = new Set(b.match(/\b[a-z]{3,}\b/gi) ?? []);
    const intersection = [...wordsA].filter((w) => wordsB.has(w)).length;
    const union = wordsA.size + wordsB.size - intersection;
    return union === 0 ? 0 : intersection / union;
  };
  const s12 = sim(last3[0], last3[1]);
  const s23 = sim(last3[1], last3[2]);
  return s12 >= threshold && s23 >= threshold;
}

async function runSelfAssessment(
  provider: ModelProvider,
  input: SelfCheckInput,
): Promise<SelfCheckVerdict> {
  const prompt =
    `You are a research progress assessor. After a tool execution, assess whether the research is making progress.\n\n` +
    `Original user request: ${input.userMessage.slice(0, 200)}\n` +
    `Last tool used: ${input.lastToolName}\n` +
    `Last tool result (first 300 chars): ${input.lastToolResult.slice(0, 300)}\n` +
    `Iterations used: ${input.iterationsUsed}/${input.maxIterations}\n\n` +
    `Assess:\n` +
    `1. Am I finding NEW information or repeating what I already know?\n` +
    `2. Is my answer getting more complete or am I hitting diminishing returns?\n` +
    `3. Should I continue this research path, pivot to a different angle, or synthesize now?\n\n` +
    `Respond with ONLY one word: CONTINUE if I should keep researching, PIVOT if I should change approach, SYNTHESIZE if I have enough to answer the user.`;

  try {
    const result = await Promise.race([
      provider.chat([{ role: "user", content: prompt }], undefined, {
        temperature: 0,
        maxTokens: 10,
      }),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("self-check timeout")), 3000),
      ),
    ]);
    const verdict = result.content.trim().toUpperCase();
    if (verdict.startsWith("CONTINUE")) return "CONTINUE";
    if (verdict.startsWith("PIVOT")) return "PIVOT";
    if (verdict.startsWith("SYNTHESIZE")) return "SYNTHESIZE";
    return "CONTINUE";
  } catch {
    return "CONTINUE";
  }
}
```

### Find the main `run()` method in `OwlEngine` (around line 400-500 — the ReAct loop)

In the loop iteration section (after tool result is received), add self-check logic:

**Find the spot after this line (approx line ~1170 in original, after tool execution)**:

```typescript
iterations++;
toolsUsed.push(toolName);
log.engine.info(`[${iterations}] Tool: ${toolName}`);
```

**Add AFTER that block**:

```typescript
// ── Self-check every N iterations ────────────────────────────
const config = context.config?.research ?? {};
const selfCheckInterval = config.selfCheckInterval ?? 5;
const maxForTask =
  context.depth === "deep"
    ? (config.maxIterations ?? DEFAULT_DEEP_MAX_TOOL_ITERATIONS)
    : DEFAULT_MAX_TOOL_ITERATIONS;
const similarityThreshold = config.similarityThreshold ?? 0.7;

if (!shouldSkipSelfCheck(iterations, selfCheckInterval)) {
  const recentResults = toolResultsBuffer.slice(-3);
  const diminishing = config.enableDiminishingReturns
    ? detectDiminishingReturns(recentResults, similarityThreshold)
    : false;

  let verdict: SelfCheckVerdict = "CONTINUE";

  if (diminishing) {
    log.engine.info(`[SelfCheck] Diminishing returns detected — forcing PIVOT`);
    verdict = "PIVOT";
  } else {
    verdict = await runSelfAssessment(provider, {
      lastToolName: toolName,
      lastToolResult: String(toolResult),
      recentToolResults: recentResults,
      userMessage,
      iterationsUsed: iterations,
      maxIterations: maxForTask,
      similarityThreshold,
    });
    log.engine.info(
      `[SelfCheck] Verdict: ${verdict} (iter ${iterations}/${maxForTask})`,
    );
  }

  if (verdict === "SYNTHESIZE" || verdict === "PIVOT") {
    const directive =
      verdict === "SYNTHESIZE"
        ? `You have gathered sufficient information. Stop calling tools and write your final comprehensive answer now. Append [DONE] at the end.`
        : `Your current approach is not yielding new information. Pivot to a different angle, search with different terms, or take a completely different approach. Do NOT repeat the same tool calls.`;
    messages.push({ role: "system", content: directive });
    break;
  }
}
```

**Note**: Add `const toolResultsBuffer: string[] = [];` before the loop, and push results after each tool call:

```typescript
toolResultsBuffer.push(String(toolResult));
if (toolResultsBuffer.length > 5) toolResultsBuffer.shift();
```

### Find the `[DONE]` signal parsing section

Add support for `[DEEPER]` marker:

**Find**:

```typescript
const stopSignals = [
  "[DONE]",
  "[STOP]",
  "[FINISHED]",
  "[COMPLETE]",
  "TERMINATE",
];
```

**Replace with**:

```typescript
const stopSignals = [
  "[DONE]",
  "[STOP]",
  "[FINISHED]",
  "[COMPLETE]",
  "TERMINATE",
  "[DEEPER]",
];
```

**Handle `[DEEPER]` after `[DONE]` processing** (around the `break` statement for DONE signals):

```typescript
if (stripped === "DEEPER") {
  if (!deeperExtended) {
    log.engine.info(
      "[SelfCheck] [DEEPER] received — extending iteration budget by 10",
    );
    maxForTask = Math.min(maxForTask + 10, 60);
    deeperExtended = true;
  }
  return true;
}
```

Add `let deeperExtended = false;` before the loop.

---

## STEP 5: `src/orchestrator/orchestrator.ts`

**Goal**: Add `executeDeepResearch()` and wire depth mode through execution.

### Add `executeDeepResearch` method BEFORE the `execute()` method (~line 85):

```typescript
// ─── DEEP RESEARCH ─────────────────────────────────────────────

/**
 * Deep research execution — pre-injects pellets, uses extended iterations,
 * and runs an evaluator-optimizer synthesis at the end.
 */
async executeDeepResearch(
  userMessage: string,
  baseContext: EngineContext,
  strategy: TaskStrategy,
  callbacks: GatewayCallbacks,
): Promise<OrchestrationResult> {
  const researchConfig = this.config.research ?? {};
  const maxIterations = researchConfig.maxIterations ?? 40;
  const subtopics = strategy.researchSignal?.subtopics ?? [];

  if (callbacks.onProgress) {
    const reason = strategy.researchSignal?.reason ?? "deep research";
    await callbacks.onProgress(
      `🔬 **Deep Research Mode** — ${reason}${subtopics.length > 0 ? ` (${subtopics.length} subtopics)` : ""}`,
    );
  }

  // ── Pre-inject relevant pellets ───────────────────────────
  let pelletContext = "";
  if (this.pelletStore) {
    try {
      const query = subtopics.length > 0 ? subtopics.join(" ") : userMessage;
      const results = await this.pelletStore.searchWithGraph(query, 3);
      if (results.length > 0) {
        pelletContext = results
          .map((r) => `## ${r.pellet.title}\n${r.pellet.content}\n`)
          .join("\n");
        if (callbacks.onProgress) {
          await callbacks.onProgress(
            `📚 Pre-injecting ${results.length} relevant pellet(s) from prior knowledge`,
          );
        }
      }
    } catch (err) {
      log.engine.warn(`[DeepResearch] Pellet pre-injection failed: ${err}`);
    }
  }

  // ── Build research context ────────────────────────────────
  const researchPrompt =
    (pelletContext ? `## Prior Knowledge (read-only, do not repeat)\n${pelletContext}\n\n` : "") +
    `## Your Task\n${userMessage}\n\n` +
    `You are conducting deep research. Use the tools available to find comprehensive information. ` +
    `Check your findings against multiple sources where possible. ` +
    `When you have gathered sufficient evidence, write a thorough, well-structured response.`;

  // ── Execute with extended iteration budget ───────────────
  const deepContext: EngineContext = {
    ...baseContext,
    maxIterations,
    depth: "deep",
    sessionHistory: pelletContext
      ? [{ role: "system", content: pelletContext }, ...baseContext.sessionHistory]
      : baseContext.sessionHistory,
    skipGapDetection: true,
  };

  const response = await this.engine.run(researchPrompt, deepContext);

  // ── Evaluator-optimizer: ask "what's missing?" ───────────
  if (strategy.researchSignal?.subtopics && strategy.researchSignal.subtopics.length > 1) {
    try {
      if (callbacks.onProgress) {
        await callbacks.onProgress(`🔍 **Evaluator: checking for gaps...**`);
      }
      const gapsPrompt =
        `You just completed research on: ${userMessage}\n\n` +
        `Your findings covered: ${strategy.researchSignal.subtopics.join(", ")}\n\n` +
        `Review your answer and identify: what aspects are NOT covered? What questions would make this research more complete?\n` +
        `Respond with a brief gap analysis (2-3 sentences max) or "none" if you covered the topic thoroughly.`;

      const gapsResponse = await this.engine.provider.chat(
        [{ role: "user", content: gapsPrompt }],
        undefined,
        { temperature: 0, maxTokens: 200 },
      );

      const gapsText = gapsResponse.content.trim().toLowerCase();
      if (gapsText !== "none" && gapsText.length > 10) {
        if (callbacks.onProgress) {
          await callbacks.onProgress(`📋 **Gap Analysis:** ${gapsResponse.content.slice(0, 200)}`);
        }
      }
    } catch {
      // Non-fatal
    }
  }

  // ── Proactive "while I was in there" offer ──────────────
  if (callbacks.onProgress) {
    const proactivePrompt =
      `Based on the research just completed about "${userMessage}", identify one surprising or interesting finding that the user might want to know about but didn't explicitly ask.\n` +
      `Respond with ONLY the finding in 1-2 sentences, or "none" if nothing notable was found.`;

    try {
      const proactiveResponse = await this.engine.provider.chat(
        [{ role: "user", content: proactivePrompt }],
        undefined,
        { temperature: 0.3, maxTokens: 100 },
      );
      const finding = proactiveResponse.content.trim();
      if (finding !== "none" && finding.length > 10) {
        await callbacks.onProgress(
          `💡 **While I was researching:** ${finding}\n_(Reply with "tell me more" to explore this)_`,
        );
      }
    } catch {
      // Non-fatal
    }
  }

  return toOrchResult(response, "STANDARD");
}
```

### Modify the `execute()` method to call `executeDeepResearch` when depth="deep"

**Find the `execute()` method** (~line 85) and add a case at the top:

```typescript
async execute(
  strategy: TaskStrategy,
  userMessage: string,
  baseContext: EngineContext,
  callbacks: GatewayCallbacks,
): Promise<OrchestrationResult> {
  // Deep research path — pre-injects pellets and uses extended iterations
  if (strategy.depth === "deep") {
    return this.executeDeepResearch(userMessage, baseContext, strategy, callbacks);
  }

  switch (strategy.strategy) {
    // ... rest of switch cases unchanged
```

---

## STEP 6: `src/engine/planner.ts`

**Goal**: Add `createDeepResearchPlan()` for decomposing research tasks.

### Add this method BEFORE the `createPlan()` method (~line 56):

````typescript
/**
 * Create a research plan — decomposes a research topic into
 * fact-gathering → comparison → analysis → synthesis phases.
 */
async createDeepResearchPlan(
  userMessage: string,
  subtopics: string[],
  availableTools: ToolDefinition[],
): Promise<TaskPlan> {
  const toolNames = availableTools.map((t) => `${t.name}: ${t.description}`);

  const subtopicList = subtopics.length > 0
    ? `Identified research subtopics:\n${subtopics.map((s, i) => `${i + 1}. ${s}`).join("\n")}\n\n`
    : "";

  const prompt =
    `You are a research planner. Decompose a research request into phased steps.\n\n` +
    `${subtopicList}` +
    `USER REQUEST: ${userMessage}\n\n` +
    `AVAILABLE TOOLS:\n${toolNames.join("\n")}\n\n` +
    `Create a research plan with these phases:\n` +
    `Phase 1: Fact-Gathering — gather primary facts, definitions, overview\n` +
    `Phase 2: Deep-Dive — explore each subtopic with targeted searches\n` +
    `Phase 3: Comparison/Analysis — compare findings, identify contradictions\n` +
    `Phase 4: Synthesis — produce comprehensive, well-structured answer\n\n` +
    `Respond with ONLY valid JSON:\n` +
    `{\n` +
    `  "goal": "one-line summary of the research goal",\n` +
    `  "estimatedComplexity": "simple" | "moderate" | "complex",\n` +
    `  "steps": [\n` +
    `    {\n` +
    `      "id": 1,\n` +
    `      "description": "what to research in this step",\n` +
    `      "toolsNeeded": ["tool_name"],\n` +
    `      "dependsOn": []\n` +
    `    }\n` +
    `  ]\n` +
    `}\n\n` +
    `Maximum 8 steps. Output ONLY valid JSON.`;

  try {
    const response = await this.provider.chat(
      [
        { role: "system", content: "You are a research planner. Output only valid JSON." },
        { role: "user", content: prompt },
      ],
      undefined,
      { temperature: 0.1 },
    );

    let jsonStr = response.content.trim();
    if (jsonStr.startsWith("```json")) jsonStr = jsonStr.replace(/^```json/, "").replace(/```$/, "").trim();
    else if (jsonStr.startsWith("```")) jsonStr = jsonStr.replace(/^```/, "").replace(/```$/, "").trim();

    const parsed = JSON.parse(jsonStr);
    return {
      goal: parsed.goal ?? userMessage.slice(0, 100),
      estimatedComplexity: parsed.estimatedComplexity ?? "complex",
      steps: (parsed.steps ?? []).map((s: any, i: number) => ({
        id: s.id ?? i + 1,
        description: s.description ?? "",
        toolsNeeded: s.toolsNeeded ?? [],
        dependsOn: s.dependsOn ?? [],
        status: "pending" as const,
      })),
    };
  } catch (err) {
    log.engine.warn(`[ResearchPlanner] Failed to parse plan: ${err}`);
    return {
      goal: userMessage.slice(0, 100),
      estimatedComplexity: "complex",
      steps: [
        { id: 1, description: "Research " + userMessage.slice(0, 80), toolsNeeded: [], dependsOn: [], status: "pending" },
      ],
    };
  }
}
````

---

## STEP 7: `src/gateway/core.ts`

**Goal**: Wire `ContinuityResult` into context builder for FOLLOW_UP/CONTINUATION.

### Find where `ContinuityResult` is computed (~line 635 in `handleCore()`):

```typescript
continuityResult = await classifyContinuity(
  message.text,
  session,
  temporalSnapshot,
  fastProvider,
);
```

### Find the `buildEngineContext()` call (~line 956) and pass `continuityResult`:

**Find**:

```typescript
const engineCtx = await this.buildEngineContext(
  session,
  callbacks,
  dynamicSkillsContext,
  isIsolatedTask,
  this.attemptLogs.get(message.sessionId),
  message.channelId,
  message.userId,
);
```

**Replace with**:

```typescript
const engineCtx = await this.buildEngineContext(
  session,
  callbacks,
  dynamicSkillsContext,
  isIsolatedTask,
  this.attemptLogs.get(message.sessionId),
  message.channelId,
  message.userId,
  continuityResult ?? null,
);
```

### Update `buildEngineContext()` signature to accept `continuityResult`

**Find the `buildEngineContext` method definition** (search for `private async buildEngineContext`):

**Update the signature**:

```typescript
private async buildEngineContext(
  session: Session,
  callbacks: GatewayCallbacks,
  dynamicSkillsContext: string,
  isIsolatedTask: boolean,
  attemptLog: AttemptLog | null,
  channelId?: string,
  userId?: string,
  continuityResult?: ContinuityResult | null,
): Promise<EngineContext> {
```

**Find where the context object is built** and add continuity enrichment:

After the existing context construction, add inside the returned object:

```typescript
// ── Continuity-based context enrichment ────────────────────
let enrichedSessionHistory = sessionHistory;
if (
  continuityResult &&
  ["CONTINUATION", "FOLLOW_UP"].includes(continuityResult.classification)
) {
  const digest = await this.ctx.digestManager?.get(session.id);
  const topicBlock = digest
    ? buildTopicMemoryBlock(continuityResult, digest)
    : null;

  if (topicBlock) {
    enrichedSessionHistory = [
      { role: "system" as const, content: topicBlock },
      ...sessionHistory,
    ];
    log.engine.info(
      `[Continuity] Injecting topic memory block for ${continuityResult.classification}`,
    );
  }
}
```

**Add this helper function in the file** (near the bottom of the file):

```typescript
/**
 * Build a topic memory block from a continuity result + conversation digest.
 * Gives the model a narrative summary of what was being discussed.
 */
function buildTopicMemoryBlock(
  result: ContinuityResult,
  digest: import("../memory/conversation-digest.js").ConversationDigest,
): string {
  const lines = [
    "## Conversation Continuity (read-only context)",
    `This is a ${result.classification} from the user's prior messages in this session.`,
  ];

  if (result.priorTopicSummary) {
    lines.push(`Prior topic: ${result.priorTopicSummary}`);
  }

  if (digest) {
    if (digest.summary) {
      lines.push(`What's been established: ${digest.summary}`);
    }
    if (digest.openQuestions && digest.openQuestions.length > 0) {
      lines.push(`Open questions: ${digest.openQuestions.join("; ")}`);
    }
    if (digest.keyFindings && digest.keyFindings.length > 0) {
      lines.push(`Key findings so far: ${digest.keyFindings.join("; ")}`);
    }
  }

  lines.push(
    "The user's current message is a follow-up to the above. Use this context to understand implicit references (it/that/this).",
  );

  return lines.join("\n");
}
```

---

## Verification Checklist

After implementing all 7 steps, run:

```bash
npm run build    # Should compile without errors
npm run lint     # Should pass with no new warnings
npm run test     # Existing tests should still pass
```

### Manual Test Cases

**Test 1: Quick query (no depth trigger)**

```
User: "what is Python?"
Expected: STANDARD strategy, depth=quick, normal iteration count
```

**Test 2: Auto-detect deep research**

```
User: "do research on the best laptops for programming, compare CPU performance, RAM, battery life, and price"
Expected: strategy=STANDARD, depth=deep, researchSignal.autoDetected=true, subtopics populated
```

**Test 3: Self-check fires**

```
User: "find all the files in my project that use async/await and analyze their error handling patterns"
Expected: Self-check verdict logged after 5th iteration, PIVOT/CONTINUE/SYNTHESIZE verdict respected
```

**Test 4: Follow-up with context**

```
Session: User researches laptops → asks "what about the battery?"
Expected: Topic memory block injected, model understands "battery" refers to laptop battery
```

---

## Rollback Plan

If issues arise, each step is independently reversible:

- Step 1: Remove `depth` and `researchSignal` fields from `TaskStrategy` interface
- Step 2: Remove `research` from config
- Step 3: Remove `detectResearchIntent()` function call from `classifyStrategy()`
- Step 4: Remove self-check injection code from runtime.ts loop
- Step 5: Remove `executeDeepResearch()` call from orchestrator
- Step 6: Remove `createDeepResearchPlan()` (only called if depth="deep")
- Step 7: Remove continuity enrichment from `buildEngineContext()`
