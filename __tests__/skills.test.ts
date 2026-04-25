import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SkillsRegistry } from "../src/skills/registry.js";
import { SkillParser, meetsRequirements } from "../src/skills/parser.js";
import { IntentRouter } from "../src/skills/intent-router.js";
import { SkillComposer } from "../src/skills/composer.js";
import { ConfigContextBuilder } from "../src/skills/config-context.js";
import { SkillTracker } from "../src/skills/tracker.js";
import { ClawHubClient, SkillSelector } from "../src/skills/clawhub.js";
import type { Skill, SkillMetadata } from "../src/skills/types.js";
import type { ModelProvider } from "../src/providers/base.js";
import type { StackOwlConfig } from "../src/config/loader.js";

// ─── Mock Logger ───────────────────────────────────────────────────────

vi.mock("../src/logger.js", () => {
  const mockWarn = vi.fn();
  const mockInfo = vi.fn();
  const mockDebug = vi.fn();
  const mockError = vi.fn();
  return {
    log: {
      engine: {
        info: mockInfo,
        warn: mockWarn,
        debug: mockDebug,
        error: mockError,
      },
    },
    Logger: vi.fn().mockImplementation(() => ({
      info: mockInfo,
      warn: mockWarn,
      debug: mockDebug,
      error: mockError,
      incoming: vi.fn(),
      outgoing: vi.fn(),
    })),
  };
});

// ─── Mock fs/promises ──────────────────────────────────────────────────

vi.mock("node:fs/promises", () => ({
  readdir: vi.fn(),
}));

// ─── Helper: Create a minimal skill ───────────────────────────────────

function makeSkill(overrides: Partial<Skill> = {}): Skill {
  return {
    name: "test_skill",
    description: "A test skill for unit testing",
    instructions: "Do something useful",
    metadata: {
      name: "test_skill",
      description: "A test skill for unit testing",
    },
    sourcePath: "/tmp/test/SKILL.md",
    enabled: true,
    ...overrides,
  };
}

type ExtendedOpenClaw = SkillMetadata["openclaw"] & {
  depends?: string[];
  chains?: string[];
};

// ─── SkillsRegistry Tests ───────────────────────────────────────────────

describe("SkillsRegistry", () => {
  let registry: SkillsRegistry;

  beforeEach(() => {
    registry = new SkillsRegistry();
  });

  describe("register / get / unregister", () => {
    it("should register and retrieve a skill by name", () => {
      const skill = makeSkill({ name: "fetch_data" });
      registry.register(skill);
      expect(registry.get("fetch_data")).toBe(skill);
    });

    it("should be case-insensitive when retrieving skills", () => {
      const skill = makeSkill({ name: "FetchData" });
      registry.register(skill);
      expect(registry.get("fetchdata")).toBe(skill);
      expect(registry.get("FETCHDATA")).toBe(skill);
    });

    it("should unregister a skill by name", () => {
      const skill = makeSkill({ name: "fetch_data" });
      registry.register(skill);
      expect(registry.unregister("fetch_data")).toBe(true);
      expect(registry.get("fetch_data")).toBeUndefined();
    });

    it("should return false when unregistering non-existent skill", () => {
      expect(registry.unregister("nonexistent")).toBe(false);
    });

    it("should return undefined for get on non-existent skill", () => {
      expect(registry.get("nonexistent")).toBeUndefined();
    });
  });

  describe("listAll / listEnabled", () => {
    it("should list all registered skills", () => {
      registry.register(makeSkill({ name: "skill1" }));
      registry.register(makeSkill({ name: "skill2" }));
      const all = registry.listAll();
      expect(all).toHaveLength(2);
    });

    it("should list only enabled skills", () => {
      registry.register(makeSkill({ name: "skill1", enabled: true }));
      registry.register(makeSkill({ name: "skill2", enabled: false }));
      const enabled = registry.listEnabled();
      expect(enabled).toHaveLength(1);
      expect(enabled[0].name).toBe("skill1");
    });
  });

  describe("enable / disable", () => {
    it("should enable a registered skill", () => {
      const skill = makeSkill({ name: "fetch_data", enabled: false });
      registry.register(skill);
      expect(registry.enable("fetch_data")).toBe(true);
      expect(registry.get("fetch_data")?.enabled).toBe(true);
    });

    it("should disable a registered skill", () => {
      const skill = makeSkill({ name: "fetch_data", enabled: true });
      registry.register(skill);
      expect(registry.disable("fetch_data")).toBe(true);
      expect(registry.get("fetch_data")?.enabled).toBe(false);
    });

    it("should return false when enabling unknown skill", () => {
      expect(registry.enable("nonexistent")).toBe(false);
    });

    it("should return false when disabling unknown skill", () => {
      expect(registry.disable("nonexistent")).toBe(false);
    });
  });

  describe("getEligible", () => {
    it("should return only enabled skills by default", () => {
      registry.register(makeSkill({ name: "enabled_skill", enabled: true }));
      registry.register(makeSkill({ name: "disabled_skill", enabled: false }));
      const eligible = registry.getEligible({});
      expect(eligible).toHaveLength(1);
      expect(eligible[0].name).toBe("enabled_skill");
    });

    it("should include skills with 'always' flag regardless of requirements", () => {
      const alwaysSkill = makeSkill({
        name: "always_skill",
        enabled: true,
        metadata: {
          name: "always_skill",
          description: "Always included",
          openclaw: { always: true },
        },
      });
      registry.register(alwaysSkill);
      const eligible = registry.getEligible({});
      expect(eligible.some((s) => s.name === "always_skill")).toBe(true);
    });

    it("should include skills with met OS requirements", () => {
      const osSkill = makeSkill({
        name: "mac_only",
        enabled: true,
        metadata: {
          name: "mac_only",
          description: "macOS only",
          openclaw: { os: ["darwin"] },
        },
      });
      registry.register(osSkill);
      const eligible = registry.getEligible({ os: "darwin" });
      expect(eligible.some((s) => s.name === "mac_only")).toBe(true);
    });

    it("should exclude skills with unmet OS requirement", () => {
      const osSkill = makeSkill({
        name: "linux_only",
        enabled: true,
        metadata: {
          name: "linux_only",
          description: "Linux only",
          openclaw: { os: ["linux"] },
        },
      });
      registry.register(osSkill);
      const eligible = registry.getEligible({ os: "darwin" });
      expect(eligible.some((s) => s.name === "linux_only")).toBe(false);
    });
  });

  describe("formatForContext", () => {
    it("should return empty string for empty skill list", () => {
      const result = registry.formatForContext([]);
      expect(result).toBe("");
    });

    it("should format a single skill as XML", () => {
      const skill = makeSkill({
        name: "fetch_data",
        description: "Fetch external data",
      });
      const result = registry.formatForContext([skill]);
      expect(result).toContain("<skill>");
      expect(result).toContain("<name>fetch_data</name>");
      expect(result).toContain(
        "<description>Fetch external data</description>",
      );
      expect(result).toContain("</skills>");
    });

    it("should format multiple skills as XML", () => {
      const skills = [
        makeSkill({ name: "skill1" }),
        makeSkill({ name: "skill2" }),
      ];
      const result = registry.formatForContext(skills);
      expect(result).toContain("<skill>");
      expect(result).toContain("<name>skill1</name>");
      expect(result).toContain("<name>skill2</name>");
      expect(result).toContain("</skills>");
    });
  });

  describe("formatForContextSingle", () => {
    it("should format a single skill without surrounding tags", () => {
      const skill = makeSkill({
        name: "fetch_data",
        description: "Fetch data",
      });
      const result = registry.formatForContextSingle(skill);
      expect(result).toContain("<skill>");
      expect(result).not.toContain("</skills>");
      expect(result).toContain("</skill>");
    });
  });

  describe("getBehavioral", () => {
    it("returns only skills with conditions, filtered by owlName", () => {
      const registry = new SkillsRegistry();

      const taskSkill = makeSkill({ name: "git_commit" });

      const behavioralAll = makeSkill({
        name: "cost_alarm",
        conditions: ["user mentions billing"],
        relevantOwls: ["*"],
        trigger: "context" as const,
        priority: "high" as const,
      });

      const behavioralScrooge = makeSkill({
        name: "budget_strict",
        conditions: ["user wants to overspend"],
        relevantOwls: ["scrooge"],
        trigger: "context" as const,
        priority: "medium" as const,
      });

      const behavioralOther = makeSkill({
        name: "other_instinct",
        conditions: ["some condition"],
        relevantOwls: ["other_owl"],
        trigger: "context" as const,
        priority: "low" as const,
      });

      registry.register(taskSkill);
      registry.register(behavioralAll);
      registry.register(behavioralScrooge);
      registry.register(behavioralOther);

      const result = registry.getBehavioral("scrooge");
      const names = result.map((s) => s.name);

      expect(names).toContain("cost_alarm");         // relevantOwls: ["*"]
      expect(names).toContain("budget_strict");      // relevantOwls: ["scrooge"]
      expect(names).not.toContain("git_commit");     // no conditions
      expect(names).not.toContain("other_instinct"); // wrong owl
    });

    it("returns empty array when no behavioral skills registered", () => {
      const registry = new SkillsRegistry();
      registry.register(makeSkill({ name: "plain" }));
      expect(registry.getBehavioral("any_owl")).toEqual([]);
    });
  });
});

// ─── SkillParser + meetsRequirements Tests ──────────────────────────────

describe("SkillParser", () => {
  describe("parseContent", () => {
    it("should parse valid SKILL.md content", () => {
      const parser = new SkillParser();
      const raw = `---
name: fetch_data
description: Fetch data from an API
---
Do the fetch operation.`;
      const skill = parser.parseContent(raw, "/tmp/fetch_data/SKILL.md");
      expect(skill.name).toBe("fetch_data");
      expect(skill.description).toBe("Fetch data from an API");
      expect(skill.instructions).toBe("Do the fetch operation.");
      expect(skill.sourcePath).toBe("/tmp/fetch_data/SKILL.md");
      expect(skill.enabled).toBe(true);
    });

    it("should throw when name is missing", () => {
      const parser = new SkillParser();
      const raw = `---
description: No name here
---
Content`;
      expect(() => parser.parseContent(raw)).toThrow(
        'missing required "name" field',
      );
    });

    it("should throw when description is missing", () => {
      const parser = new SkillParser();
      const raw = `---
name: my_skill
---
Content`;
      expect(() => parser.parseContent(raw)).toThrow(
        'missing required "description" field',
      );
    });

    it("should parse structured steps", () => {
      const parser = new SkillParser();
      const raw = `---
name: multi_step
description: Multi-step skill
steps:
  - id: fetch
    tool: http_get
    args:
      url: "{{url}}"
  - id: parse
    tool: jq
    args:
      filter: ".data"
---
Content`;
      const skill = parser.parseContent(raw);
      expect(skill.steps).toHaveLength(2);
      expect(skill.steps![0].id).toBe("fetch");
      expect(skill.steps![0].tool).toBe("http_get");
      expect(skill.steps![1].id).toBe("parse");
    });

    it("should parse structured parameters", () => {
      const parser = new SkillParser();
      const raw = `---
name: parameterized
description: A parameterized skill
parameters:
  url:
    type: string
    description: The URL to fetch
    required: true
---
Content`;
      const skill = parser.parseContent(raw);
      expect(skill.parameters?.url.type).toBe("string");
      expect(skill.parameters?.url.required).toBe(true);
    });
  });

  describe("behavioral field parsing", () => {
    it("parses conditions, trigger, relevant_owls, priority from frontmatter", () => {
      const parser = new SkillParser();
      const raw = `---
name: cost_alarm
description: Warn about cost implications
trigger: context
conditions:
  - "user mentions cloud costs"
  - "user compares managed vs self-hosted"
relevant_owls:
  - "scrooge"
  - "*"
priority: high
---
Act on your cost-alarm instinct.
`;
      const skill = parser.parseContent(raw, "/tmp/cost_alarm/SKILL.md");
      expect(skill.trigger).toBe("context");
      expect(skill.conditions).toEqual([
        "user mentions cloud costs",
        "user compares managed vs self-hosted",
      ]);
      expect(skill.relevantOwls).toEqual(["scrooge", "*"]);
      expect(skill.priority).toBe("high");
    });

    it("leaves behavioral fields undefined when absent", () => {
      const parser = new SkillParser();
      const raw = `---
name: git_commit
description: Create a git commit
---
Stage and commit changes.
`;
      const skill = parser.parseContent(raw, "/tmp/git_commit/SKILL.md");
      expect(skill.trigger).toBeUndefined();
      expect(skill.conditions).toBeUndefined();
      expect(skill.relevantOwls).toBeUndefined();
      expect(skill.priority).toBeUndefined();
    });

    it("defaults trigger to 'context' when conditions present but trigger absent", () => {
      const parser = new SkillParser();
      const raw = `---
name: cost_alarm
description: Cost warning
conditions:
  - "user mentions billing"
---
Warn about costs.
`;
      const skill = parser.parseContent(raw, "/tmp/cost_alarm/SKILL.md");
      expect(skill.trigger).toBe("context");
      expect(skill.conditions).toEqual(["user mentions billing"]);
    });
  });
});

describe("meetsRequirements", () => {
  it("should return satisfied=true when no requirements", () => {
    const skill = makeSkill();
    const result = meetsRequirements(skill, {});
    expect(result.satisfied).toBe(true);
    expect(result.missing).toHaveLength(0);
  });

  it("should return satisfied=false when OS requirement not met", () => {
    const skill = makeSkill({
      metadata: {
        name: "test",
        description: "test",
        openclaw: { os: ["linux"] },
      },
    });
    const result = meetsRequirements(skill, { os: "darwin" });
    expect(result.satisfied).toBe(false);
    expect(result.missing.some((m) => m.includes("darwin"))).toBe(true);
  });

  it("should return satisfied=false when binary is missing", () => {
    const skill = makeSkill({
      metadata: {
        name: "test",
        description: "test",
        openclaw: { requires: { bins: ["curl"] } },
      },
    });
    const result = meetsRequirements(skill, { bins: ["jq"] });
    expect(result.satisfied).toBe(false);
    expect(result.missing).toContain("binary: curl");
  });

  it("should return satisfied=false when env variable is missing", () => {
    const skill = makeSkill({
      metadata: {
        name: "test",
        description: "test",
        openclaw: { requires: { env: ["API_KEY"] } },
      },
    });
    const result = meetsRequirements(skill, { env: {} });
    expect(result.satisfied).toBe(false);
    expect(result.missing).toContain("env: API_KEY");
  });

  it("should return satisfied=false when config key is missing", () => {
    const skill = makeSkill({
      metadata: {
        name: "test",
        description: "test",
        openclaw: { requires: { config: ["telegram.botToken"] } },
      },
    });
    const result = meetsRequirements(skill, { config: {} });
    expect(result.satisfied).toBe(false);
    expect(result.missing).toContain("config: telegram.botToken");
  });
});

// ─── IntentRouter Tests ────────────────────────────────────────────────

describe("IntentRouter", () => {
  let registry: SkillsRegistry;
  let mockProvider: ModelProvider;

  beforeEach(() => {
    registry = new SkillsRegistry();
    mockProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({
        content: "skill1",
        model: "mock",
        finishReason: "stop" as const,
      }),
      chatWithTools: vi.fn(),
      chatStream: vi.fn(),
      embed: vi
        .fn()
        .mockResolvedValue({ embedding: [0.1, 0.2, 0.3], model: "mock" }),
      listModels: vi.fn().mockResolvedValue([]),
      healthCheck: vi.fn().mockResolvedValue(true),
    } as unknown as ModelProvider;
  });

  describe("constructor", () => {
    it("should create an IntentRouter with a registry", () => {
      const router = new IntentRouter(registry);
      expect(router).toBeDefined();
    });

    it("should accept an optional provider and tracker", () => {
      const tracker = new SkillTracker("/tmp/test-workspace");
      const router = new IntentRouter(registry, mockProvider, tracker);
      expect(router).toBeDefined();
    });
  });

  describe("reindex", () => {
    it("should not throw when reindexing enabled skills", () => {
      registry.register(makeSkill({ name: "fetch_data", enabled: true }));
      registry.register(makeSkill({ name: "parse_json", enabled: true }));
      const router = new IntentRouter(registry);
      router.reindex();
      expect(true).toBe(true);
    });

    it("should not throw when all skills are disabled", () => {
      registry.register(makeSkill({ name: "disabled_skill", enabled: false }));
      const router = new IntentRouter(registry);
      router.reindex();
      expect(true).toBe(true);
    });
  });

  describe("route", () => {
    it("should return empty array when no skills registered", async () => {
      const router = new IntentRouter(registry);
      const results = await router.route("help me fetch data from an API");
      expect(results).toHaveLength(0);
    });

    it("should return results within maxResults limit", async () => {
      registry.register(makeSkill({ name: "fetch_data" }));
      registry.register(makeSkill({ name: "parse_json" }));
      registry.register(makeSkill({ name: "send_email" }));
      const router = new IntentRouter(registry);
      const results = await router.route("fetch some JSON", 2);
      expect(results.length).toBeLessThanOrEqual(2);
    });
  });

  describe("clearCache", () => {
    it("should not throw when clearing cache", () => {
      const router = new IntentRouter(registry);
      router.clearCache();
      expect(true).toBe(true);
    });
  });

  describe("precomputeEmbeddings", () => {
    it("should not throw when provider is not available", async () => {
      const router = new IntentRouter(registry);
      await router.precomputeEmbeddings();
      expect(true).toBe(true);
    });

    it("should call embed for enabled skills when provider available", async () => {
      registry.register(makeSkill({ name: "fetch_data" }));
      const router = new IntentRouter(registry, mockProvider);
      await router.precomputeEmbeddings();
      expect(mockProvider.embed).toHaveBeenCalled();
    });
  });
});

// ─── SkillTracker Tests ───────────────────────────────────────────────

describe("SkillTracker", () => {
  let tracker: SkillTracker;
  const testDir = "/tmp/test-tracker-" + Date.now();

  beforeEach(() => {
    tracker = new SkillTracker(testDir);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe("recordSelection", () => {
    it("should increment selection count", () => {
      tracker.recordSelection("fetch_data");
      tracker.recordSelection("fetch_data");
      const stats = tracker.getStats("fetch_data");
      expect(stats?.selectionCount).toBe(2);
    });
  });

  describe("recordSuccess", () => {
    it("should increment success count and update avgDurationMs", () => {
      tracker.recordSelection("fetch_data");
      tracker.recordSuccess("fetch_data", 100);
      tracker.recordSuccess("fetch_data", 200);
      const stats = tracker.getStats("fetch_data");
      expect(stats?.successCount).toBe(2);
      expect(stats?.avgDurationMs).toBe(150);
      expect(stats?.successRate).toBe(1);
    });
  });

  describe("recordFailure", () => {
    it("should increment failure count and update successRate", () => {
      tracker.recordSelection("fetch_data");
      tracker.recordFailure("fetch_data", 50);
      const stats = tracker.getStats("fetch_data");
      expect(stats?.failureCount).toBe(1);
      expect(stats?.successRate).toBe(0);
    });
  });

  describe("getSuccessRate", () => {
    it("should return undefined for unknown skill", () => {
      expect(tracker.getSuccessRate("nonexistent")).toBeUndefined();
    });

    it("should return 1.0 for all-successful skill", () => {
      tracker.recordSelection("fetch_data");
      tracker.recordSuccess("fetch_data", 100);
      expect(tracker.getSuccessRate("fetch_data")).toBe(1);
    });

    it("should return 0.5 for partially successful skill", () => {
      tracker.recordSelection("fetch_data");
      tracker.recordSuccess("fetch_data", 100);
      tracker.recordSelection("fetch_data");
      tracker.recordFailure("fetch_data", 100);
      expect(tracker.getSuccessRate("fetch_data")).toBe(0.5);
    });
  });

  describe("getUsageMultiplier", () => {
    it("should return a multiplier for never-used skill", () => {
      const multiplier = tracker.getUsageMultiplier("never_used");
      expect(multiplier).toBeGreaterThan(0);
    });

    it("should return higher multiplier for recently successful skill", () => {
      tracker.recordSelection("good_skill");
      tracker.recordSuccess("good_skill", 100);
      const multiplier = tracker.getUsageMultiplier("good_skill");
      expect(multiplier).toBeGreaterThan(0.7);
    });
  });

  describe("getTopSkills", () => {
    it("should return skills sorted by selection count", () => {
      tracker.recordSelection("skill_a");
      tracker.recordSelection("skill_a");
      tracker.recordSelection("skill_b");
      const top = tracker.getTopSkills(5);
      expect(top[0].name).toBe("skill_a");
      expect(top[1].name).toBe("skill_b");
    });
  });

  describe("getFailingSkills", () => {
    it("should return empty array when no failing skills", () => {
      const failing = tracker.getFailingSkills();
      expect(failing).toHaveLength(0);
    });

    it("should identify skills with low success rate after sufficient selections", () => {
      tracker.recordSelection("bad_skill");
      tracker.recordSelection("bad_skill");
      tracker.recordSelection("bad_skill");
      tracker.recordFailure("bad_skill", 100);
      tracker.recordFailure("bad_skill", 100);
      tracker.recordFailure("bad_skill", 100);
      const failing = tracker.getFailingSkills(3, 0.3);
      expect(failing.some((f) => f.name === "bad_skill")).toBe(true);
    });
  });
});

// ─── SkillComposer Tests ────────────────────────────────────────────────

describe("SkillComposer", () => {
  let registry: SkillsRegistry;
  let composer: SkillComposer;

  beforeEach(() => {
    registry = new SkillsRegistry();
    composer = new SkillComposer(registry);
  });

  describe("resolve — single skill (no dependencies)", () => {
    it("should return single-stage plan for skill with no composition", () => {
      const skill = makeSkill({ name: "fetch_data" });
      const plan = composer.resolve(skill);
      expect(plan.stages).toHaveLength(1);
      expect(plan.stages[0].label).toBe("primary");
      expect(plan.totalSkills).toBe(1);
      expect(plan.primarySkill).toBe("fetch_data");
    });
  });

  describe("resolve — skill with before dependencies", () => {
    it("should create dependency stage before primary stage", () => {
      const depSkill = makeSkill({ name: "fetch_data", enabled: true });
      registry.register(depSkill);

      const primarySkill = makeSkill({
        name: "generate_report",
        metadata: {
          name: "generate_report",
          description: "Generate report",
          openclaw: { depends: ["fetch_data"] } as ExtendedOpenClaw,
        },
      });

      const plan = composer.resolve(primarySkill);
      expect(plan.stages.length).toBeGreaterThanOrEqual(2);
      const depStage = plan.stages.find((s) => s.label === "dependencies");
      expect(depStage).toBeDefined();
      expect(depStage!.skills.some((s) => s.name === "fetch_data")).toBe(true);
    });

    it("should skip missing dependencies with warning", () => {
      const primarySkill = makeSkill({
        name: "generate_report",
        metadata: {
          name: "generate_report",
          description: "Generate report",
          openclaw: { depends: ["nonexistent_skill"] } as ExtendedOpenClaw,
        },
      });

      const plan = composer.resolve(primarySkill);
      // Should still return a plan (missing deps are skipped, warning is logged)
      expect(plan.primarySkill).toBe("generate_report");
      // Verify the plan was returned without throwing
      expect(plan.stages.length).toBeGreaterThanOrEqual(1);
    });

    it("should skip disabled dependencies", () => {
      const depSkill = makeSkill({ name: "fetch_data", enabled: false });
      registry.register(depSkill);

      const primarySkill = makeSkill({
        name: "generate_report",
        metadata: {
          name: "generate_report",
          description: "Generate report",
          openclaw: { depends: ["fetch_data"] } as ExtendedOpenClaw,
        },
      });

      // The resolve should handle disabled deps gracefully without throwing
      const plan = composer.resolve(primarySkill);
      // Deps stage may exist but the disabled skill should not be in it
      const depStage = plan.stages.find((s) => s.label === "dependencies");
      if (depStage) {
        expect(depStage.skills.some((s) => s.name === "fetch_data")).toBe(
          false,
        );
      }
    });
  });

  describe("resolve — skill with chains", () => {
    it("should create chains stage after primary stage", () => {
      const chainSkill = makeSkill({ name: "send_email", enabled: true });
      registry.register(chainSkill);

      const primarySkill = makeSkill({
        name: "generate_report",
        metadata: {
          name: "generate_report",
          description: "Generate report",
          openclaw: { chains: ["send_email"] } as ExtendedOpenClaw,
        },
      });

      const plan = composer.resolve(primarySkill);
      const chainStage = plan.stages.find((s) => s.label === "chains");
      expect(chainStage).toBeDefined();
      expect(chainStage!.skills.some((s) => s.name === "send_email")).toBe(
        true,
      );
    });

    it("should skip disabled chained skills", () => {
      const chainSkill = makeSkill({ name: "send_email", enabled: false });
      registry.register(chainSkill);

      const primarySkill = makeSkill({
        name: "generate_report",
        metadata: {
          name: "generate_report",
          description: "Generate report",
          openclaw: { chains: ["send_email"] } as ExtendedOpenClaw,
        },
      });

      const plan = composer.resolve(primarySkill);
      // Chains stage should not include disabled skill
      const chainStage = plan.stages.find((s) => s.label === "chains");
      if (chainStage) {
        expect(chainStage.skills.some((s) => s.name === "send_email")).toBe(
          false,
        );
      }
    });
  });

  describe("resolve — cycle detection", () => {
    it("should detect circular dependency and fallback to single-stage", () => {
      const skillA = makeSkill({
        name: "skill_a",
        metadata: {
          name: "skill_a",
          description: "Skill A",
          openclaw: { chains: ["skill_b"] } as ExtendedOpenClaw,
        },
      });

      const skillB = makeSkill({
        name: "skill_b",
        metadata: {
          name: "skill_b",
          description: "Skill B",
          openclaw: { chains: ["skill_a"] } as ExtendedOpenClaw,
        },
      });

      registry.register(skillA);
      registry.register(skillB);

      // This should not throw - it should detect cycle and fallback
      const plan = composer.resolve(skillA);
      expect(plan.stages).toHaveLength(1);
    });
  });

  describe("formatForContext", () => {
    it("should format single-stage plan as <skill> tag", () => {
      const skill = makeSkill({ name: "fetch_data" });
      const plan = composer.resolve(skill);
      const ctx = composer.formatForContext(plan);
      expect(ctx).toContain("<skill>");
      expect(ctx).toContain("</skill>");
      expect(ctx).not.toContain("<skill-chain>");
    });

    it("should format multi-stage plan as <skill-chain> tag", () => {
      const depSkill = makeSkill({ name: "fetch_data", enabled: true });
      registry.register(depSkill);

      const primarySkill = makeSkill({
        name: "generate_report",
        metadata: {
          name: "generate_report",
          description: "Generate report",
          openclaw: { depends: ["fetch_data"] } as ExtendedOpenClaw,
        },
      });

      const plan = composer.resolve(primarySkill);
      const ctx = composer.formatForContext(plan);
      expect(ctx).toContain("<skill-chain");
      expect(ctx).toContain('primary="generate_report"');
      expect(ctx).toContain("<stage");
    });
  });
});

// ─── ConfigContextBuilder Tests ────────────────────────────────────────

describe("ConfigContextBuilder", () => {
  let config: StackOwlConfig;
  let builder: ConfigContextBuilder;

  beforeEach(() => {
    config = {
      providers: {
        ollama: { baseUrl: "http://localhost:11434", defaultModel: "llama3" },
        anthropic: {
          apiKey: "test-key",
          defaultModel: "claude-3-5-sonnet-latest",
        },
      },
      defaultProvider: "ollama",
      defaultModel: "llama3",
      workspace: "/tmp/test-workspace",
      gateway: { port: 3000, host: "localhost" },
      parliament: { maxRounds: 3, maxOwls: 3 },
      heartbeat: { enabled: false, intervalMinutes: 60 },
      owlDna: { enabled: true, evolutionBatchSize: 10, decayRatePerWeek: 0.1 },
      telegram: { botToken: "test-token" },
      slack: {},
      execution: { hostMode: true },
      browser: { enabled: true },
      skills: { enabled: true },
      smartRouting: { enabled: true },
      costs: { enabled: true },
      storage: { backend: "sqlite" },
    } as unknown as StackOwlConfig;
    builder = new ConfigContextBuilder(config);
  });

  describe("build", () => {
    it("should build platform snapshot with providers", () => {
      const snapshot = builder.build();
      expect(snapshot.providers.length).toBeGreaterThan(0);
      expect(snapshot.providers[0]).toContain("ollama");
    });

    it("should include workspace path", () => {
      const snapshot = builder.build();
      expect(snapshot.workspacePath).toBe("/tmp/test-workspace");
    });

    it("should detect telegram adapter when botToken is present", () => {
      const snapshot = builder.build();
      expect(snapshot.adapters).toContain("telegram");
    });

    it("should detect slack adapter when botToken is present", () => {
      const configWithSlack = {
        ...config,
        slack: { botToken: "slack-token" },
      } as StackOwlConfig;
      const builderWithSlack = new ConfigContextBuilder(configWithSlack);
      const snapshot = builderWithSlack.build();
      expect(snapshot.adapters).toContain("slack");
    });

    it("should include capability flags", () => {
      const snapshot = builder.build();
      expect(snapshot.capabilities).toContain("host_shell_access");
      expect(snapshot.capabilities).toContain("skill_system");
      expect(snapshot.capabilities).toContain("smart_routing");
      expect(snapshot.capabilities).toContain("sqlite_storage");
    });
  });

  describe("toPromptBlock", () => {
    it("should include available LLM providers section", () => {
      const block = builder.toPromptBlock();
      expect(block).toContain("Available LLM providers");
      expect(block).toContain("ollama");
    });

    it("should include workspace path", () => {
      const block = builder.toPromptBlock();
      expect(block).toContain("/tmp/test-workspace");
    });

    it("should include IMPORTANT warning about tool names", () => {
      const block = builder.toPromptBlock();
      expect(block).toContain("IMPORTANT");
      expect(block).toContain("tool names");
    });

    it("should include telegram-specific tool guidance when adapter present", () => {
      const block = builder.toPromptBlock();
      expect(block).toContain("send_telegram_message");
    });

    it("should handle empty providers gracefully", () => {
      const emptyConfig = {
        ...config,
        providers: {},
      } as StackOwlConfig;
      const emptyBuilder = new ConfigContextBuilder(emptyConfig);
      const block = emptyBuilder.toPromptBlock();
      expect(block).not.toContain("Available LLM providers:");
    });
  });
});

// ─── SkillSelector Tests ─────────────────────────────────────────────

describe("SkillSelector", () => {
  it("should register skills for matching", () => {
    const selector = new SkillSelector();
    selector.register({
      name: "fetch_data",
      description: "Fetch data from API",
      instructions: "Use http_get tool",
    });
    const results = selector.findRelevant("I need to fetch some data");
    expect(results).toContain("fetch_data");
  });

  it("should return top N results", () => {
    const selector = new SkillSelector();
    selector.register({
      name: "skill1",
      description: "Test skill 1",
      instructions: "",
    });
    selector.register({
      name: "skill2",
      description: "Test skill 2",
      instructions: "",
    });
    const results = selector.findRelevant("test", 1);
    expect(results).toHaveLength(1);
  });

  it("should give bonus to name matches", () => {
    const selector = new SkillSelector();
    selector.register({
      name: "fetch_data",
      description: "Something else",
      instructions: "",
    });
    const results = selector.findRelevant("fetch_data");
    expect(results[0]).toBe("fetch_data");
  });

  it("should clear all registered skills", () => {
    const selector = new SkillSelector();
    selector.register({
      name: "skill1",
      description: "Test",
      instructions: "",
    });
    selector.clear();
    const results = selector.findRelevant("test");
    expect(results).toHaveLength(0);
  });
});

// ─── ClawHubClient Tests ─────────────────────────────────────────────

describe("ClawHubClient", () => {
  it("should be constructable", () => {
    const client = new ClawHubClient();
    expect(client).toBeDefined();
  });

  it("should accept custom config", () => {
    const client = new ClawHubClient({
      siteUrl: "https://custom.clawhub.ai",
      registryUrl: "https://custom.registry.ai",
    });
    expect(client).toBeDefined();
  });
});

// ─── Module Exports Tests ──────────────────────────────────────────────

describe("Skills module exports", () => {
  it("should export SkillsLoader", async () => {
    const { SkillsLoader } = await import("../src/skills/index.js");
    expect(SkillsLoader).toBeDefined();
  });

  it("should export SkillsRegistry", async () => {
    const { SkillsRegistry } = await import("../src/skills/index.js");
    expect(SkillsRegistry).toBeDefined();
  });

  it("should export SkillParser and meetsRequirements", async () => {
    const { SkillParser, meetsRequirements: mr } =
      await import("../src/skills/index.js");
    expect(SkillParser).toBeDefined();
    expect(typeof mr).toBe("function");
  });

  it("should export IntentRouter", async () => {
    const { IntentRouter } = await import("../src/skills/index.js");
    expect(IntentRouter).toBeDefined();
  });

  it("should export SkillTracker", async () => {
    const { SkillTracker } = await import("../src/skills/index.js");
    expect(SkillTracker).toBeDefined();
  });

  it("should export SkillComposer", async () => {
    const { SkillComposer } = await import("../src/skills/index.js");
    expect(SkillComposer).toBeDefined();
  });

  it("should export SkillSelector", async () => {
    const { SkillSelector } = await import("../src/skills/index.js");
    expect(SkillSelector).toBeDefined();
  });

  it("should export skill classes and functions from index", async () => {
    const mod = (await import("../src/skills/index.js")) as any;
    expect(mod.SkillsLoader).toBeDefined();
    expect(mod.SkillsRegistry).toBeDefined();
    expect(mod.IntentRouter).toBeDefined();
    expect(mod.SkillTracker).toBeDefined();
    expect(mod.SkillComposer).toBeDefined();
    expect(mod.SkillParser).toBeDefined();
    expect(mod.SkillSelector).toBeDefined();
    expect(mod.ClawHubClient).toBeDefined();
  });
});
