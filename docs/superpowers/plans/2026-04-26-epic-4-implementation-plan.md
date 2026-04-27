# Epic 4: Tool Mastery & Delegation - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 10 stories (4.1-4.10) for Tool Mastery & Delegation system

**Architecture:**
- Tool Mastery layer: tool-selector, fallback-sequencer, fallback-discoverer, tool-mastery
- Delegation layer: domain-tool-map, delegation-decider, subowl-spawner, subowl-executor, result-synthesizer
- Integration with existing infrastructure: decomposer.ts, sub-owl-runner.ts, approach-library.ts

**Tech Stack:** TypeScript ES2023, NodeNext modules, Vitest

---

## Story Files to Create

All story files go in `_bmad-output/implementation-artifacts/`

- `4-1-learned-effectiveness-tool-selection.md`
- `4-2-learned-fallback-sequences.md`
- `4-3-new-fallback-discovery.md`
- `4-4-per-tool-mastery-awareness.md`
- `4-5-dynamic-domain-tool-map-updates.md`
- `4-6-task-decomposition.md`
- `4-7-subowlrunner-spawning.md`
- `4-8-sub-owl-tool-execution.md`
- `4-9-result-synthesis.md`
- `4-10-delegation-decision-by-complexity.md`

---

## Module Map

| Story | Module | Path |
|-------|--------|------|
| 4.1 | ToolSelector | `src/tools/tool-selector.ts` |
| 4.2 | FallbackSequencer | `src/tools/fallback-sequencer.ts` |
| 4.3 | FallbackDiscoverer | `src/tools/fallback-discoverer.ts` |
| 4.4 | ToolMastery | `src/tools/tool-mastery.ts` |
| 4.5 | DomainToolMap | `src/delegation/domain-tool-map.ts` |
| 4.6 | TaskDecomposer | `src/delegation/decomposer.ts` (exists - needs tests) |
| 4.7 | SubOwlSpawner | `src/delegation/subowl-spawner.ts` |
| 4.8 | SubOwlExecutor | `src/delegation/subowl-executor.ts` |
| 4.9 | ResultSynthesizer | `src/delegation/result-synthesizer.ts` |
| 4.10 | DelegationDecider | `src/delegation/delegation-decider.ts` |

---

## Task 1: Story 4.1 - Learned Effectiveness Tool Selection

**Files:**
- Create: `_bmad-output/implementation-artifacts/4-1-learned-effectiveness-tool-selection.md`
- Create: `src/tools/tool-selector.ts`
- Create: `__tests__/tools/tool-selector.test.ts`

- [ ] **Step 1: Create story file 4-1-learned-effectiveness-tool-selection.md**

```markdown
# Story 4.1: Learned Effectiveness Tool Selection

Status: ready-for-dev

## Story

As an owl that learns,
I want to select the appropriate tool for a given task based on learned effectiveness, not just recency,
So that I can apply past learnings to new tool selection decisions.

## Acceptance Criteria

1. **Given** a new task requires tool selection
   **When** the ToolSelector evaluates options
   **Then** the learned effectiveness score from ApproachLibrary influences tool selection weight
   **And** the selection does not depend solely on recency

2. **Given** a task type with no prior history
   **When** a tool is selected
   **Then** a default heuristic is used as a fallback
   **And** the selection is recorded for future learning

## Tasks / Subtasks

- [ ] Task 1: Implement ToolSelector class
  - [ ] Subtask 1.1: Define ToolSelectionContext interface
  - [ ] Subtask 1.2: Implement selectTool() with effectiveness weighting
  - [ ] Subtask 1.3: Integrate with ApproachLibrary for effectiveness scores
- [ ] Task 2: Write unit tests
  - [ ] Subtask 2.1: Test effectiveness weighting
  - [ ] Subtask 2.2: Test fallback for no-history tasks
```

- [ ] **Step 2: Create src/tools/tool-selector.ts**

```typescript
import type { ToolDefinition } from "../providers/base.js";
import type { ApproachPattern } from "../learning/approach-library.js";
import { log } from "../logger.js";

export interface ToolSelectionContext {
  taskType: string;
  availableTools: ToolDefinition[];
  owlName: string;
}

export interface ToolSelectionResult {
  selectedTool: ToolDefinition;
  effectivenessScore: number;
  alternatives: Array<{ tool: ToolDefinition; score: number }>;
}

export class ToolSelector {
  constructor(
    private getEffectivenessScore: (
      owlName: string,
      toolName: string,
      taskType: string,
    ) => number,
    private getPatterns: (
      owlName: string,
      toolName: string,
      taskType: string,
    ) => ApproachPattern | undefined,
  ) {}

  selectTool(context: ToolSelectionContext): ToolSelectionResult {
    const { taskType, availableTools, owlName } = context;

    const scored = availableTools.map((tool) => {
      const score = this.getEffectivenessScore(owlName, tool.name, taskType);
      return { tool, score };
    });

    scored.sort((a, b) => b.score - a.score);

    const selectedTool = scored[0].tool;
    const alternatives = scored.slice(1).map((s) => ({
      tool: s.tool,
      score: s.score,
    }));

    log.engine.debug(
      `[ToolSelector] Selected ${selectedTool.name} (score=${scored[0].score}) for ${taskType}`,
    );

    return {
      selectedTool,
      effectivenessScore: scored[0].score,
      alternatives,
    };
  }
}
```

- [ ] **Step 3: Create __tests__/tools/tool-selector.test.ts**

```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import { ToolSelector } from "../../src/tools/tool-selector.js";
import type { ToolDefinition } from "../../src/providers/base.js";
import type { ApproachPattern } from "../../src/learning/approach-library.js";

describe("ToolSelector", () => {
  let mockGetEffectivenessScore: ReturnType<typeof vi.fn>;
  let mockGetPatterns: ReturnType<typeof vi.fn>;
  let selector: ToolSelector;
  let mockTools: ToolDefinition[];

  beforeEach(() => {
    mockGetEffectivenessScore = vi.fn();
    mockGetPatterns = vi.fn();
    selector = new ToolSelector(mockGetEffectivenessScore, mockGetPatterns);

    mockTools = [
      { name: "web_search", description: "Search the web" },
      { name: "read_file", description: "Read a file" },
      { name: "shell", description: "Run shell commands" },
    ] as ToolDefinition[];
  });

  describe("selectTool", () => {
    it("selects tool with highest effectiveness score", () => {
      mockGetEffectivenessScore.mockImplementation((owl, tool, task) => {
        if (tool === "web_search") return 0.9;
        if (tool === "read_file") return 0.7;
        return 0.5;
      });

      const result = selector.selectTool({
        taskType: "research",
        availableTools: mockTools,
        owlName: "Hoot",
      });

      expect(result.selectedTool.name).toBe("web_search");
      expect(result.effectivenessScore).toBe(0.9);
    });

    it("returns alternatives sorted by score", () => {
      mockGetEffectivenessScore.mockImplementation((owl, tool, task) => {
        if (tool === "web_search") return 0.9;
        if (tool === "read_file") return 0.7;
        return 0.5;
      });

      const result = selector.selectTool({
        taskType: "research",
        availableTools: mockTools,
        owlName: "Hoot",
      });

      expect(result.alternatives).toHaveLength(2);
      expect(result.alternatives[0].tool.name).toBe("read_file");
      expect(result.alternatives[1].tool.name).toBe("shell");
    });

    it("uses default score when no history exists", () => {
      mockGetEffectivenessScore.mockReturnValue(0.5);

      const result = selector.selectTool({
        taskType: "unknown_task",
        availableTools: mockTools,
        owlName: "Hoot",
      });

      expect(result.effectivenessScore).toBe(0.5);
    });
  });
});
```

- [ ] **Step 4: Run tests to verify they fail (missing module)**

Run: `npx vitest run __tests__/tools/tool-selector.test.ts`
Expected: FAIL - module not found

- [ ] **Step 5: Create the src/tools/tool-selector.ts module**

Run: `mkdir -p src/tools && cat > src/tools/tool-selector.ts << 'EOF'
// Implementation from Step 2 above
EOF`

- [ ] **Step 6: Run tests to verify they pass**

Run: `npx vitest run __tests__/tools/tool-selector.test.ts`
Expected: PASS

---

## Task 2: Story 4.2 - Learned Fallback Sequences

**Files:**
- Create: `_bmad-output/implementation-artifacts/4-2-learned-fallback-sequences.md`
- Create: `src/tools/fallback-sequencer.ts`
- Create: `__tests__/tools/fallback-sequencer.test.ts`

- [ ] **Step 1: Create story file 4-2-learned-fallback-sequences.md**

```markdown
# Story 4.2: Learned Fallback Sequences

Status: ready-for-dev

## Story

As an owl that learns,
I want to apply learned fallback sequences when a tool fails, not static ones,
So that I can recover from failures more effectively based on past experience.

## Acceptance Criteria

1. **Given** a tool has failed during execution
   **When** the FallbackSequencer evaluates recovery options
   **Then** it retrieves the learned fallback sequence from history
   **And** applies the most effective fallback based on past outcomes

2. **Given** no learned fallback exists for a tool/task combination
   **When** a tool fails
   **Then** a default fallback sequence is used
   **And** the outcome is recorded for future learning
```

- [ ] **Step 2: Create src/tools/fallback-sequencer.ts**

```typescript
import { log } from "../logger.js";

export interface FallbackSequence {
  toolName: string;
  fallbackOrder: string[];
  learnedFrom: "static" | "discovered";
}

export interface FallbackOutcome {
  sequence: string[];
  success: boolean;
  failureReason?: string;
}

export class FallbackSequencer {
  private learnedSequences: Map<string, FallbackSequence> = new Map();
  private outcomeHistory: Map<string, FallbackOutcome[]> = new Map();

  private key(owlName: string, toolName: string, taskType: string): string {
    return `${owlName}::${toolName}::${taskType}`;
  }

  recordFallbackOutcome(
    owlName: string,
    toolName: string,
    taskType: string,
    sequence: string[],
    success: boolean,
    failureReason?: string,
  ): void {
    const k = this.key(owlName, toolName, taskType);
    const outcomes = this.outcomeHistory.get(k) ?? [];
    outcomes.push({ sequence, success, failureReason });
    this.outcomeHistory.set(k, outcomes);

    this.updateLearnedSequence(owlName, toolName, taskType, sequence, success);
  }

  private updateLearnedSequence(
    owlName: string,
    toolName: string,
    taskType: string,
    sequence: string[],
    success: boolean,
  ): void {
    const k = this.key(owlName, toolName, taskType);
    const existing = this.learnedSequences.get(k);

    if (!existing || success) {
      this.learnedSequences.set(k, {
        toolName,
        fallbackOrder: sequence,
        learnedFrom: "discovered",
      });
    }
  }

  getFallbackSequence(
    owlName: string,
    toolName: string,
    taskType: string,
  ): string[] {
    const k = this.key(owlName, toolName, taskType);
    const learned = this.learnedSequences.get(k);

    if (learned) {
      log.engine.debug(
        `[FallbackSequencer] Using learned sequence for ${toolName}: ${learned.fallbackOrder.join(" -> ")}`,
      );
      return learned.fallbackOrder;
    }

    return this.getDefaultSequence(toolName);
  }

  private getDefaultSequence(toolName: string): string[] {
    const defaults: Record<string, string[]> = {
      web_fetch: ["web_search", "pellet_recall", "recall"],
      read_file: ["shell", "pellet_recall", "recall"],
      write_file: ["shell", "remember"],
      shell: ["read_file", "recall"],
      default: ["recall", "remember", "pellet_recall"],
    };

    return defaults[toolName] ?? defaults["default"];
  }
}
```

- [ ] **Step 3: Create __tests__/tools/fallback-sequencer.test.ts**

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { FallbackSequencer } from "../../src/tools/fallback-sequencer.js";

describe("FallbackSequencer", () => {
  let sequencer: FallbackSequencer;

  beforeEach(() => {
    sequencer = new FallbackSequencer();
  });

  describe("getFallbackSequence", () => {
    it("returns learned sequence when available", () => {
      sequencer.recordFallbackOutcome(
        "Hoot",
        "web_fetch",
        "research",
        ["web_search", "recall"],
        true,
      );

      const sequence = sequencer.getFallbackSequence("Hoot", "web_fetch", "research");
      expect(sequence).toEqual(["web_search", "recall"]);
    });

    it("returns default sequence when no history", () => {
      const sequence = sequencer.getFallbackSequence(
        "Hoot",
        "web_fetch",
        "unknown_task",
      );

      expect(sequence).toContain("web_search");
      expect(sequence).toContain("pellet_recall");
    });

    it("records failed sequence for future learning", () => {
      sequencer.recordFallbackOutcome(
        "Hoot",
        "read_file",
        "debugging",
        ["shell", "recall"],
        false,
        "shell failed too",
      );

      const sequence = sequencer.getFallbackSequence("Hoot", "read_file", "debugging");
      expect(sequence).toEqual(["shell", "recall"]);
    });
  });

  describe("recordFallbackOutcome", () => {
    it("stores outcome history", () => {
      sequencer.recordFallbackOutcome(
        "Hoot",
        "web_fetch",
        "research",
        ["web_search"],
        true,
      );

      const sequence = sequencer.getFallbackSequence("Hoot", "web_fetch", "research");
      expect(sequence).toEqual(["web_search"]);
    });
  });
});
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `npx vitest run __tests__/tools/fallback-sequencer.test.ts`
Expected: FAIL

- [ ] **Step 5: Create the src/tools/fallback-sequencer.ts module**

- [ ] **Step 6: Run tests to verify they pass**

---

## Task 3: Story 4.3 - New Fallback Discovery

**Files:**
- Create: `_bmad-output/implementation-artifacts/4-3-new-fallback-discovery.md`
- Create: `src/tools/fallback-discoverer.ts`
- Create: `__tests__/tools/fallback-discoverer.test.ts`

- [ ] **Step 1: Create story file 4-3-new-fallback-discovery.md**

```markdown
# Story 4.3: New Fallback Discovery

Status: ready-for-dev

## Story

As an owl that learns,
I want to discover and record new fallback paths when existing ones fail,
So that I can improve my recovery strategies over time.

## Acceptance Criteria

1. **Given** a fallback sequence has been attempted and failed
   **When** the FallbackDiscoverer analyzes the failure
   **Then** it proposes alternative tool combinations
   **And** records the new fallback path if successful

2. **Given** a new fallback path is discovered
   **When** it leads to successful task completion
   **Then** the path is stored in the learned sequences
   **And** future fallback selection can use this path
```

- [ ] **Step 2: Create src/tools/fallback-discoverer.ts**

```typescript
import { log } from "../logger.js";

export interface DiscoveredPath {
  sequence: string[];
  successRate: number;
  attemptCount: number;
  lastAttempt: string;
}

export class FallbackDiscoverer {
  private discoveredPaths: Map<string, DiscoveredPath[]> = new Map();

  private key(toolName: string, taskType: string): string {
    return `${toolName}::${taskType}`;
  }

  recordAttempt(
    toolName: string,
    taskType: string,
    sequence: string[],
    success: boolean,
  ): void {
    const k = this.key(toolName, taskType);
    const paths = this.discoveredPaths.get(k) ?? [];

    const existingPath = paths.find(
      (p) => p.sequence.join("->") === sequence.join("->"),
    );

    if (existingPath) {
      existingPath.attemptCount++;
      existingPath.successRate = success
        ? (existingPath.successRate * (existingPath.attemptCount - 1) + 1) /
          existingPath.attemptCount
        : (existingPath.successRate * (existingPath.attemptCount - 1)) /
          existingPath.attemptCount;
      existingPath.lastAttempt = new Date().toISOString();
    } else {
      paths.push({
        sequence: [...sequence],
        successRate: success ? 1 : 0,
        attemptCount: 1,
        lastAttempt: new Date().toISOString(),
      });
    }

    this.discoveredPaths.set(k, paths);

    log.engine.debug(
      `[FallbackDiscoverer] Recorded attempt for ${toolName}/${taskType}: ${sequence.join(" -> ")} (success=${success})`,
    );
  }

  getBestPath(toolName: string, taskType: string): string[] | null {
    const k = this.key(toolName, taskType);
    const paths = this.discoveredPaths.get(k);

    if (!paths || paths.length === 0) return null;

    const sorted = [...paths].sort((a, b) => {
      if (b.attemptCount < 3 && a.attemptCount >= 3) return 1;
      if (a.attemptCount < 3 && b.attemptCount >= 3) return -1;
      return b.successRate - a.successRate;
    });

    return sorted[0].sequence;
  }

  getAllPaths(toolName: string, taskType: string): DiscoveredPath[] {
    const k = this.key(toolName, taskType);
    return this.discoveredPaths.get(k) ?? [];
  }
}
```

- [ ] **Step 3: Create __tests__/tools/fallback-discoverer.test.ts**

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { FallbackDiscoverer } from "../../src/tools/fallback-discoverer.js";

describe("FallbackDiscoverer", () => {
  let discoverer: FallbackDiscoverer;

  beforeEach(() => {
    discoverer = new FallbackDiscoverer();
  });

  describe("recordAttempt", () => {
    it("records first attempt", () => {
      discoverer.recordAttempt("web_fetch", "research", ["web_search"], true);

      const paths = discoverer.getAllPaths("web_fetch", "research");
      expect(paths).toHaveLength(1);
      expect(paths[0].sequence).toEqual(["web_search"]);
      expect(paths[0].successRate).toBe(1);
      expect(paths[0].attemptCount).toBe(1);
    });

    it("updates existing path statistics", () => {
      discoverer.recordAttempt("web_fetch", "research", ["web_search"], true);
      discoverer.recordAttempt("web_fetch", "research", ["web_search"], false);

      const paths = discoverer.getAllPaths("web_fetch", "research");
      expect(paths).toHaveLength(1);
      expect(paths[0].attemptCount).toBe(2);
      expect(paths[0].successRate).toBe(0.5);
    });

    it("tracks multiple different paths", () => {
      discoverer.recordAttempt("web_fetch", "research", ["web_search"], true);
      discoverer.recordAttempt("web_fetch", "research", ["pellet_recall"], false);

      const paths = discoverer.getAllPaths("web_fetch", "research");
      expect(paths).toHaveLength(2);
    });
  });

  describe("getBestPath", () => {
    it("returns path with highest success rate", () => {
      discoverer.recordAttempt("web_fetch", "research", ["web_search"], true);
      discoverer.recordAttempt("web_fetch", "research", ["pellet_recall"], false);

      const best = discoverer.getBestPath("web_fetch", "research");
      expect(best).toEqual(["web_search"]);
    });

    it("prefers paths with more attempts", () => {
      discoverer.recordAttempt("web_fetch", "research", ["path_a"], true);
      discoverer.recordAttempt("web_fetch", "research", ["path_b"], true);
      discoverer.recordAttempt("web_fetch", "research", ["path_b"], true);

      const best = discoverer.getBestPath("web_fetch", "research");
      expect(best).toEqual(["path_b"]);
    });

    it("returns null when no paths recorded", () => {
      const best = discoverer.getBestPath("unknown", "task");
      expect(best).toBeNull();
    });
  });
});
```

---

## Task 4: Story 4.4 - Per-Tool Mastery Awareness

**Files:**
- Create: `_bmad-output/implementation-artifacts/4-4-per-tool-mastery-awareness.md`
- Create: `src/tools/tool-mastery.ts`
- Create: `__tests__/tools/tool-mastery.test.ts`

- [ ] **Step 1: Create story file 4-4-per-tool-mastery-awareness.md**

```markdown
# Story 4.4: Per-Tool Mastery Awareness

Status: ready-for-dev

## Story

As an owl,
I want to be aware of my own mastery level per tool and adjust confidence accordingly,
So that I can accurately represent my capabilities and avoid overconfident mistakes.

## Acceptance Criteria

1. **Given** an owl has used a tool multiple times
   **When** the ToolMastery evaluates mastery level
   **Then** it returns a mastery rating (novice/intermediate/expert/master)
   **And** the rating affects confidence in tool selection

2. **Given** a mastery level is determined
   **When** generating a response
   **Then** the confidence score is adjusted based on mastery
   **And** expert-level tools have higher confidence weights
```

- [ ] **Step 2: Create src/tools/tool-mastery.ts**

```typescript
export type MasteryLevel = "novice" | "intermediate" | "expert" | "master";

export interface ToolMasteryProfile {
  toolName: string;
  masteryLevel: MasteryLevel;
  confidenceMultiplier: number;
  totalAttempts: number;
  successRate: number;
}

export class ToolMastery {
  private masteryProfiles: Map<string, ToolMasteryProfile> = new Map();

  private calculateMasteryLevel(
    totalAttempts: number,
    successRate: number,
  ): MasteryLevel {
    if (totalAttempts < 3) return "novice";
    if (successRate >= 0.9 && totalAttempts >= 20) return "master";
    if (successRate >= 0.75 && totalAttempts >= 10) return "expert";
    if (totalAttempts >= 5 || successRate >= 0.5) return "intermediate";
    return "novice";
  }

  private confidenceMultiplier(level: MasteryLevel): number {
    const multipliers: Record<MasteryLevel, number> = {
      novice: 0.6,
      intermediate: 0.8,
      expert: 1.0,
      master: 1.2,
    };
    return multipliers[level];
  }

  recordAttempt(toolName: string, success: boolean): void {
    const profile = this.masteryProfiles.get(toolName) ?? {
      toolName,
      masteryLevel: "novice" as MasteryLevel,
      confidenceMultiplier: 0.6,
      totalAttempts: 0,
      successRate: 0,
    };

    profile.totalAttempts++;
    profile.successRate =
      (profile.successRate * (profile.totalAttempts - 1) +
        (success ? 1 : 0)) /
      profile.totalAttempts;
    profile.masteryLevel = this.calculateMasteryLevel(
      profile.totalAttempts,
      profile.successRate,
    );
    profile.confidenceMultiplier = this.confidenceMultiplier(
      profile.masteryLevel,
    );

    this.masteryProfiles.set(toolName, profile);
  }

  getMasteryProfile(toolName: string): ToolMasteryProfile {
    return (
      this.masteryProfiles.get(toolName) ?? {
        toolName,
        masteryLevel: "novice",
        confidenceMultiplier: 0.6,
        totalAttempts: 0,
        successRate: 0,
      }
    );
  }

  getConfidenceMultiplier(toolName: string): number {
    return this.getMasteryProfile(toolName).confidenceMultiplier;
  }
}
```

- [ ] **Step 3: Create __tests__/tools/tool-mastery.test.ts**

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { ToolMastery, MasteryLevel } from "../../src/tools/tool-mastery.js";

describe("ToolMastery", () => {
  let mastery: ToolMastery;

  beforeEach(() => {
    mastery = new ToolMastery();
  });

  describe("recordAttempt", () => {
    it("starts at novice level", () => {
      const profile = mastery.getMasteryProfile("web_search");
      expect(profile.masteryLevel).toBe("novice");
      expect(profile.confidenceMultiplier).toBe(0.6);
    });

    it("promotes to intermediate after 5 attempts with 50% success", () => {
      for (let i = 0; i < 5; i++) {
        mastery.recordAttempt("shell", i < 3);
      }
      const profile = mastery.getMasteryProfile("shell");
      expect(profile.masteryLevel).toBe("intermediate");
    });

    it("promotes to expert after 10 attempts with 75% success", () => {
      for (let i = 0; i < 10; i++) {
        mastery.recordAttempt("read_file", i < 8);
      }
      const profile = mastery.getMasteryProfile("read_file");
      expect(profile.masteryLevel).toBe("expert");
    });

    it("promotes to master after 20 attempts with 90% success", () => {
      for (let i = 0; i < 20; i++) {
        mastery.recordAttempt("write_file", i < 18);
      }
      const profile = mastery.getMasteryProfile("write_file");
      expect(profile.masteryLevel).toBe("master");
    });
  });

  describe("getConfidenceMultiplier", () => {
    it("returns novice multiplier for new tools", () => {
      expect(mastery.getConfidenceMultiplier("unknown")).toBe(0.6);
    });

    it("returns correct multiplier for expert level", () => {
      for (let i = 0; i < 15; i++) {
        mastery.recordAttempt("web_fetch", true);
      }
      expect(mastery.getConfidenceMultiplier("web_fetch")).toBeGreaterThanOrEqual(1.0);
    });
  });
});
```

---

## Task 5: Story 4.5 - Dynamic DOMAIN_TOOL_MAP Updates

**Files:**
- Create: `_bmad-output/implementation-artifacts/4-5-dynamic-domain-tool-map-updates.md`
- Create: `src/delegation/domain-tool-map.ts`
- Create: `__tests__/delegation/domain-tool-map.test.ts`

- [ ] **Step 1: Create story file 4-5-dynamic-domain-tool-map-updates.md**

```markdown
# Story 4.5: Dynamic DOMAIN_TOOL_MAP Updates

Status: ready-for-dev

## Story

As an owl,
I want to update the DOMAIN_TOOL_MAP based on accumulated success/failure outcomes,
So that tool recommendations improve over time based on real performance data.

## Acceptance Criteria

1. **Given** tool execution outcomes have been recorded
   **When** the DomainToolMap receives success/failure data
   **Then** it updates tool rankings for relevant domains
   **And** higher-performing tools get priority in the map

2. **Given** a domain query is made
   **When** tools are requested for that domain
   **Then** the returned tool list reflects learned effectiveness
   **And** tools are sorted by learned performance
```

- [ ] **Step 2: Create src/delegation/domain-tool-map.ts**

```typescript
import { log } from "../logger.js";

export interface DomainToolRanking {
  toolName: string;
  successRate: number;
  totalAttempts: number;
  lastUsed: string;
}

export class DomainToolMap {
  private domainToolMap: Record<string, string[]> = {
    research: ["web_fetch", "web_search", "recall", "pellet_recall"],
    coding: ["read_file", "write_file", "shell"],
    memory: ["recall", "remember", "pellet_recall"],
    filesystem: ["read_file", "write_file", "shell"],
    web: ["web_fetch", "web_search"],
    analysis: ["recall", "pellet_recall", "read_file"],
    communication: ["send_file"],
  };

  private toolStats: Map<string, Map<string, DomainToolRanking>> = new Map();

  recordOutcome(
    domain: string,
    toolName: string,
    success: boolean,
  ): void {
    if (!this.toolStats.has(domain)) {
      this.toolStats.set(domain, new Map());
    }

    const domainStats = this.toolStats.get(domain)!;
    const stats = domainStats.get(toolName) ?? {
      toolName,
      successRate: 0,
      totalAttempts: 0,
      lastUsed: "",
    };

    stats.totalAttempts++;
    stats.successRate =
      (stats.successRate * (stats.totalAttempts - 1) +
        (success ? 1 : 0)) /
      stats.totalAttempts;
    stats.lastUsed = new Date().toISOString();

    domainStats.set(toolName, stats);

    log.engine.debug(
      `[DomainToolMap] Updated ${domain}/${toolName}: rate=${stats.successRate.toFixed(2)}`,
    );
  }

  getToolsForDomain(domain: string): string[] {
    const baseTools = this.domainToolMap[domain] ?? ["recall"];
    const domainStats = this.toolStats.get(domain);

    if (!domainStats) return baseTools;

    const ranked: Array<{ tool: string; rate: number }> = [];

    for (const tool of baseTools) {
      const stats = domainStats.get(tool);
      ranked.push({
        tool,
        rate: stats?.successRate ?? 0.5,
      });
    }

    ranked.sort((a, b) => b.rate - a.rate);
    return ranked.map((r) => r.tool);
  }

  getToolStats(domain: string, toolName: string): DomainToolRanking | null {
    return this.toolStats.get(domain)?.get(toolName) ?? null;
  }
}
```

- [ ] **Step 3: Create __tests__/delegation/domain-tool-map.test.ts**

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { DomainToolMap } from "../../src/delegation/domain-tool-map.js";

describe("DomainToolMap", () => {
  let map: DomainToolMap;

  beforeEach(() => {
    map = new DomainToolMap();
  });

  describe("getToolsForDomain", () => {
    it("returns base tools when no stats", () => {
      const tools = map.getToolsForDomain("research");
      expect(tools).toContain("web_fetch");
      expect(tools).toContain("web_search");
    });

    it("sorts by success rate when stats available", () => {
      map.recordOutcome("research", "web_fetch", false);
      map.recordOutcome("research", "web_fetch", false);
      map.recordOutcome("research", "web_search", true);
      map.recordOutcome("research", "web_search", true);

      const tools = map.getToolsForDomain("research");
      expect(tools[0]).toBe("web_search");
    });
  });

  describe("recordOutcome", () => {
    it("updates success rate correctly", () => {
      map.recordOutcome("coding", "read_file", true);
      map.recordOutcome("coding", "read_file", true);
      map.recordOutcome("coding", "read_file", false);

      const stats = map.getToolStats("coding", "read_file");
      expect(stats?.successRate).toBeCloseTo(0.667, 2);
      expect(stats?.totalAttempts).toBe(3);
    });
  });
});
```

---

## Task 6: Story 4.6 - Task Decomposition (Existing - Add Tests)

**Files:**
- Create: `_bmad-output/implementation-artifacts/4-6-task-decomposition.md`
- Create: `__tests__/delegation/task-decomposer.test.ts`

- [ ] **Step 1: Create story file 4-6-task-decomposition.md**

- [ ] **Step 2: Create __tests__/delegation/task-decomposer.test.ts**

```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import { TaskDecomposer } from "../../src/delegation/decomposer.js";
import type { ModelProvider } from "../../src/providers/base.js";

describe("TaskDecomposer", () => {
  let mockProvider: ModelProvider;
  let decomposer: TaskDecomposer;

  beforeEach(() => {
    mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: JSON.stringify([
          {
            id: "t1",
            description: "Research the topic",
            tools: ["web_search", "web_fetch"],
            dependsOn: [],
            expectedOutput: "Research findings",
          },
          {
            id: "t2",
            description: "Write summary",
            tools: ["write_file"],
            dependsOn: ["t1"],
            expectedOutput: "Summary document",
          },
        ]),
      }),
    } as unknown as ModelProvider;

    decomposer = new TaskDecomposer(mockProvider);
  });

  describe("decompose", () => {
    it("returns a valid decomposition plan", async () => {
      const plan = await decomposer.decompose(
        "Research topic X and write a summary",
      );

      expect(plan.subtasks).toHaveLength(2);
      expect(plan.parallelGroups).toBeDefined();
      expect(plan.originalTask).toBe("Research topic X and write a summary");
    });

    it("handles LLM parse errors with fallback", async () => {
      mockProvider.chat = vi.fn().mockResolvedValue({
        content: "invalid json",
      });

      const plan = await decomposer.decompose("Simple task");

      expect(plan.subtasks).toHaveLength(1);
      expect(plan.subtasks[0].id).toBe("t1");
    });
  });
});
```

---

## Task 7: Story 4.7 - SubOwlRunner Spawning (Existing - Add Tests)

**Files:**
- Create: `_bmad-output/implementation-artifacts/4-7-subowlrunner-spawning.md`
- Create: `__tests__/delegation/sub-owl-runner.test.ts`

- [ ] **Step 1: Create story file 4-7-subowlrunner-spawning.md**

- [ ] **Step 2: Create __tests__/delegation/sub-owl-runner.test.ts**

```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import { SubOwlRunner } from "../../src/delegation/sub-owl-runner.js";
import type { DecompositionPlan } from "../../src/delegation/decomposer.js";
import type { ModelProvider } from "../../src/providers/base.js";

describe("SubOwlRunner", () => {
  let mockProvider: ModelProvider;
  let runner: SubOwlRunner;

  beforeEach(() => {
    mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: "Subtask completed successfully",
      }),
    } as unknown as ModelProvider;

    runner = new SubOwlRunner(mockProvider, "a helpful assistant");
  });

  describe("runAll", () => {
    it("executes all subtasks in parallel groups", async () => {
      const plan: DecompositionPlan = {
        originalTask: "Multi-step research",
        subtasks: [
          {
            id: "t1",
            description: "Search for info",
            tools: ["web_search"],
            dependsOn: [],
            expectedOutput: "Search results",
          },
          {
            id: "t2",
            description: "Fetch details",
            tools: ["web_fetch"],
            dependsOn: [],
            expectedOutput: "Page content",
          },
        ],
        parallelGroups: [["t1", "t2"]],
        totalSteps: 2,
      };

      const result = await runner.runAll(plan);

      expect(result.subtaskResults).toHaveLength(2);
      expect(result.successRate).toBeGreaterThan(0);
    });

    it("respects dependency ordering", async () => {
      const plan: DecompositionPlan = {
        originalTask: "Sequential task",
        subtasks: [
          {
            id: "t1",
            description: "First step",
            tools: ["shell"],
            dependsOn: [],
            expectedOutput: "Step 1 result",
          },
          {
            id: "t2",
            description: "Second step",
            tools: ["shell"],
            dependsOn: ["t1"],
            expectedOutput: "Step 2 result",
          },
        ],
        parallelGroups: [["t1"], ["t2"]],
        totalSteps: 2,
      };

      const result = await runner.runAll(plan);

      expect(result.subtaskResults).toHaveLength(2);
      expect(result.subtaskResults[0].taskId).toBe("t1");
      expect(result.subtaskResults[1].taskId).toBe("t2");
    });
  });
});
```

---

## Task 8: Story 4.8 - Sub-Owl Tool Execution

**Files:**
- Create: `_bmad-output/implementation-artifacts/4-8-sub-owl-tool-execution.md`
- Create: `src/delegation/subowl-executor.ts`
- Create: `__tests__/delegation/subowl-executor.test.ts`

- [ ] **Step 1: Create story file 4-8-sub-owl-tool-execution.md**

- [ ] **Step 2: Create src/delegation/subowl-executor.ts**

```typescript
import type { ToolImplementation, ToolContext } from "../tools/registry.js";
import type { SubTask } from "./decomposer.js";
import { log } from "../logger.js";

export interface SubOwlExecutionResult {
  taskId: string;
  success: boolean;
  output: string;
  toolsUsed: string[];
  error?: string;
}

export class SubOwlExecutor {
  constructor(private toolRegistry: Map<string, ToolImplementation>) {}

  async executeSubtask(task: SubTask, context: ToolContext): Promise<SubOwlExecutionResult> {
    const toolsUsed: string[] = [];
    let lastOutput = "";

    for (const toolName of task.tools) {
      const tool = this.toolRegistry.get(toolName);
      if (!tool) {
        log.engine.warn(`[SubOwlExecutor] Tool ${toolName} not found`);
        continue;
      }

      try {
        const result = await tool.execute({}, context);
        lastOutput = result;
        toolsUsed.push(toolName);

        if (result.includes("[Failed") || result.includes("error")) {
          log.engine.debug(`[SubOwlExecutor] Tool ${toolName} returned failure indicator`);
        }
      } catch (err) {
        log.engine.warn(`[SubOwlExecutor] Tool ${toolName} threw: ${err}`);
      }
    }

    return {
      taskId: task.id,
      success: toolsUsed.length > 0,
      output: lastOutput || `[No output from tools: ${task.tools.join(", ")}]`,
      toolsUsed,
    };
  }
}
```

- [ ] **Step 3: Create __tests__/delegation/subowl-executor.test.ts**

```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import { SubOwlExecutor } from "../../src/delegation/subowl-executor.js";
import type { ToolImplementation } from "../../src/tools/registry.js";

describe("SubOwlExecutor", () => {
  let executor: SubOwlExecutor;
  let mockToolRegistry: Map<string, ToolImplementation>;
  let mockTool: ToolImplementation;

  beforeEach(() => {
    mockTool = {
      definition: { name: "test_tool", description: "Test tool" },
      execute: vi.fn().mockResolvedValue("Tool executed successfully"),
    } as unknown as ToolImplementation;

    mockToolRegistry = new Map([["test_tool", mockTool]]);
    executor = new SubOwlExecutor(mockToolRegistry);
  });

  describe("executeSubtask", () => {
    it("executes all tools in the task", async () => {
      const result = await executor.executeSubtask(
        {
          id: "t1",
          description: "Test task",
          tools: ["test_tool"],
          dependsOn: [],
          expectedOutput: "result",
        },
        { cwd: "/tmp" },
      );

      expect(result.success).toBe(true);
      expect(result.toolsUsed).toContain("test_tool");
      expect(result.output).toBe("Tool executed successfully");
    });

    it("returns failure when no tools available", async () => {
      const executorEmpty = new SubOwlExecutor(new Map());

      const result = await executorEmpty.executeSubtask(
        {
          id: "t1",
          description: "Test task",
          tools: ["nonexistent"],
          dependsOn: [],
          expectedOutput: "result",
        },
        { cwd: "/tmp" },
      );

      expect(result.success).toBe(false);
      expect(result.output).toContain("No output");
    });
  });
});
```

---

## Task 9: Story 4.9 - Result Synthesis

**Files:**
- Create: `_bmad-output/implementation-artifacts/4-9-result-synthesis.md`
- Create: `src/delegation/result-synthesizer.ts`
- Create: `__tests__/delegation/result-synthesizer.test.ts`

- [ ] **Step 1: Create story file 4-9-result-synthesis.md**

- [ ] **Step 2: Create src/delegation/result-synthesizer.ts**

```typescript
import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { SubOwlResult } from "./sub-owl-runner.js";
import { log } from "../logger.js";

export interface SynthesisOptions {
  includeFailedResults: boolean;
  maxResultLength: number;
}

export class ResultSynthesizer {
  constructor(private provider: ModelProvider) {}

  async synthesize(
    originalTask: string,
    results: SubOwlResult[],
    options: Partial<SynthesisOptions> = {},
  ): Promise<string> {
    const opts: SynthesisOptions = {
      includeFailedResults: false,
      maxResultLength: 2000,
      ...options,
    };

    const filteredResults = opts.includeFailedResults
      ? results
      : results.filter((r) => r.success);

    if (filteredResults.length === 0) {
      return "No successful results to synthesize.";
    }

    const resultBlock = filteredResults
      .map((r) => {
        const truncated = r.output.slice(0, opts.maxResultLength);
        return `**Subtask ${r.taskId}** (${r.success ? "✓" : "✗"}): ${r.description}\n${truncated}`;
      })
      .join("\n\n---\n\n");

    const messages: ChatMessage[] = [
      {
        role: "system",
        content:
          "You are a synthesis AI. Combine multiple subtask results into a single coherent response. " +
          "Be direct. Do not repeat structure - just deliver the final answer.",
      },
      {
        role: "user",
        content:
          `Original task: "${originalTask}"\n\n` +
          `Subtask results:\n\n${resultBlock}\n\n` +
          `Synthesize into a final answer.`,
      },
    ];

    try {
      const response = await this.provider.chat(messages);
      return response.content.trim();
    } catch (err) {
      log.engine.warn(`[ResultSynthesizer] LLM synthesis failed: ${err}`);
      return this.fallbackSynthesize(filteredResults);
    }
  }

  private fallbackSynthesize(results: SubOwlResult[]): string {
    return results.map((r) => r.output).join("\n\n");
  }
}
```

- [ ] **Step 3: Create __tests__/delegation/result-synthesizer.test.ts**

```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import { ResultSynthesizer } from "../../src/delegation/result-synthesizer.js";
import type { ModelProvider } from "../../src/providers/base.js";
import type { SubOwlResult } from "../../src/delegation/sub-owl-runner.js";

describe("ResultSynthesizer", () => {
  let mockProvider: ModelProvider;
  let synthesizer: ResultSynthesizer;
  let mockResults: SubOwlResult[];

  beforeEach(() => {
    mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: "Synthesized response based on all subtasks.",
      }),
    } as unknown as ModelProvider;

    synthesizer = new ResultSynthesizer(mockProvider);

    mockResults = [
      {
        taskId: "t1",
        description: "Search for info",
        output: "Found relevant data about topic",
        success: true,
        iterations: 2,
        durationMs: 500,
      },
      {
        taskId: "t2",
        description: "Analyze data",
        output: "Analysis complete: key insights identified",
        success: true,
        iterations: 1,
        durationMs: 300,
      },
    ];
  });

  describe("synthesize", () => {
    it("calls LLM with formatted results", async () => {
      const result = await synthesizer.synthesize(
        "Research and analyze topic X",
        mockResults,
      );

      expect(mockProvider.chat).toHaveBeenCalled();
      const messages = (mockProvider.chat as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(messages[1].content).toContain("Research and analyze topic X");
      expect(messages[1].content).toContain("t1");
      expect(messages[1].content).toContain("t2");
    });

    it("filters failed results by default", async () => {
      const mixedResults: SubOwlResult[] = [
        ...mockResults,
        {
          taskId: "t3",
          description: "Failed step",
          output: "[Failed: timeout]",
          success: false,
          iterations: 0,
          durationMs: 0,
        },
      ];

      await synthesizer.synthesize("Task with failure", mixedResults);

      const messages = (mockProvider.chat as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(messages[1].content).not.toContain("Failed");
    });

    it("includes failed results when option set", async () => {
      const mixedResults: SubOwlResult[] = [
        ...mockResults,
        {
          taskId: "t3",
          description: "Failed step",
          output: "[Failed: timeout]",
          success: false,
          iterations: 0,
          durationMs: 0,
        },
      ];

      await synthesizer.synthesize("Task with failure", mixedResults, {
        includeFailedResults: true,
      });

      const messages = (mockProvider.chat as ReturnType<typeof vi.fn>).mock.calls[0][0];
      expect(messages[1].content).toContain("Failed");
    });

    it("returns fallback when LLM fails", async () => {
      mockProvider.chat = vi.fn().mockRejectedValue(new Error("LLM unavailable"));

      const result = await synthesizer.synthesize("Task", mockResults);

      expect(result).toContain("Found relevant data");
      expect(result).toContain("Analysis complete");
    });
  });
});
```

---

## Task 10: Story 4.10 - Delegation Decision by Complexity

**Files:**
- Create: `_bmad-output/implementation-artifacts/4-10-delegation-decision-by-complexity.md`
- Create: `src/delegation/delegation-decider.ts`
- Create: `__tests__/delegation/delegation-decider.test.ts`

- [ ] **Step 1: Create story file 4-10-delegation-decision-by-complexity.md**

- [ ] **Step 2: Create src/delegation/delegation-decider.ts**

```typescript
import { log } from "../logger.js";

export type ExecutionMode = "direct" | "delegated";

export interface DelegationDecision {
  mode: ExecutionMode;
  reasoning: string;
  estimatedParallelTasks?: number;
  complexityScore: number;
}

export interface ComplexityIndicators {
  hasMultipleSteps: boolean;
  hasDependencyChains: boolean;
  requiresDifferentDomains: boolean;
  estimatedSubtasks: number;
  hasUncertainty: boolean;
}

export class DelegationDecider {
  private readonly COMPLEXITY_THRESHOLD = 0.6;
  private readonly SUBTASK_THRESHOLD = 3;

  assessComplexity(task: string, indicators: Partial<ComplexityIndicators>): number {
    let score = 0;

    if (indicators.estimatedSubtasks && indicators.estimatedSubtasks >= this.SUBTASK_THRESHOLD) {
      score += 0.3;
    }
    if (indicators.hasDependencyChains) {
      score += 0.2;
    }
    if (indicators.requiresDifferentDomains) {
      score += 0.2;
    }
    if (indicators.hasUncertainty) {
      score += 0.15;
    }
    if (task.length > 500) {
      score += 0.15;
    }

    return Math.min(1, score);
  }

  decide(
    task: string,
    indicators: Partial<ComplexityIndicators> = {},
  ): DelegationDecision {
    const complexityScore = this.assessComplexity(task, indicators);

    const shouldDelegate =
      complexityScore >= this.COMPLEXITY_THRESHOLD ||
      (indicators.estimatedSubtasks ?? 0) >= this.SUBTASK_THRESHOLD;

    const decision: DelegationDecision = {
      mode: shouldDelegate ? "delegated" : "direct",
      reasoning: shouldDelegate
        ? `High complexity (${complexityScore.toFixed(2)}) suggests delegation`
        : `Low complexity (${complexityScore.toFixed(2)}) - direct execution preferred`,
      complexityScore,
    };

    if (shouldDelegate) {
      decision.estimatedParallelTasks = Math.min(
        indicators.estimatedSubtasks ?? 2,
        5,
      );
    }

    log.engine.debug(
      `[DelegationDecider] Task complexity=${complexityScore.toFixed(2)} → ${decision.mode}`,
    );

    return decision;
  }
}
```

- [ ] **Step 3: Create __tests__/delegation/delegation-decider.test.ts**

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { DelegationDecider } from "../../src/delegation/delegation-decider.js";

describe("DelegationDecider", () => {
  let decider: DelegationDecider;

  beforeEach(() => {
    decider = new DelegationDecider();
  });

  describe("assessComplexity", () => {
    it("returns low score for simple tasks", () => {
      const score = decider.assessComplexity("Simple task", {});
      expect(score).toBeLessThan(0.5);
    });

    it("returns high score for multi-step tasks", () => {
      const score = decider.assessComplexity(
        "Research and write a comprehensive report",
        { estimatedSubtasks: 5 },
      );
      expect(score).toBeGreaterThanOrEqual(0.3);
    });

    it("increases score for cross-domain tasks", () => {
      const score = decider.assessComplexity("Complex multi-domain task", {
        requiresDifferentDomains: true,
      });
      expect(score).toBeGreaterThanOrEqual(0.2);
    });
  });

  describe("decide", () => {
    it("returns direct for low complexity", () => {
      const decision = decider.decide("Simple one-step task");
      expect(decision.mode).toBe("direct");
      expect(decision.complexityScore).toBeLessThan(0.6);
    });

    it("returns delegated for high complexity", () => {
      const decision = decider.decide("Research, analyze, write, and review", {
        estimatedSubtasks: 5,
        hasDependencyChains: true,
      });
      expect(decision.mode).toBe("delegated");
      expect(decision.estimatedParallelTasks).toBeDefined();
    });

    it("includes reasoning in decision", () => {
      const decision = decider.decide("Task");
      expect(decision.reasoning).toContain("complexity");
    });
  });
});
```

---

## Task 11: Integration and Validation

- [ ] **Step 1: Run build**

Run: `npm run build`
Expected: No TypeScript errors

- [ ] **Step 2: Run all new tests**

Run: `npx vitest run __tests__/tools/__tests__/delegation/`
Expected: All tests pass

- [ ] **Step 3: Run full test suite**

Run: `npx vitest run`
Expected: No regressions

---

## Change Log

- 2026-04-26: Initial implementation plan created
