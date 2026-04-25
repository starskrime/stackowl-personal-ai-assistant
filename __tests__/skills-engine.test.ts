import { describe, it, expect, vi } from "vitest";
import { SkillsEngine } from "../src/skills/engine.js";
import type { Skill } from "../src/skills/types.js";
import type { ModelProvider } from "../src/providers/base.js";

vi.mock("../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
  },
}));

function makeProvider(responseContent: string): ModelProvider {
  return {
    chat: vi.fn().mockResolvedValue({ content: responseContent }),
  } as unknown as ModelProvider;
}

function makeBehavioralSkill(overrides: Partial<Skill> = {}): Skill {
  return {
    name: "cost_alarm",
    description: "Warn about costs",
    instructions: "Act on cost alarm.",
    metadata: { name: "cost_alarm", description: "Warn about costs" },
    sourcePath: "/tmp/cost_alarm/SKILL.md",
    enabled: true,
    trigger: "context",
    conditions: ["user mentions billing"],
    relevantOwls: ["*"],
    priority: "high",
    ...overrides,
  };
}

describe("SkillsEngine", () => {
  it("returns null when no skills provided", async () => {
    const engine = new SkillsEngine();
    const provider = makeProvider('{"triggered": false, "skillId": null}');
    const result = await engine.evaluate("hello world", [], {
      provider,
      owl: { persona: { name: "test" } } as any,
      config: {} as any,
    });
    expect(result).toBeNull();
    expect(provider.chat).not.toHaveBeenCalled();
  });

  it("returns the triggered skill when LLM says triggered=true", async () => {
    const engine = new SkillsEngine();
    const skill = makeBehavioralSkill();
    const provider = makeProvider(
      '{"triggered": true, "skillId": "cost_alarm"}',
    );
    const result = await engine.evaluate("how much does this cloud setup cost?", [skill], {
      provider,
      owl: { persona: { name: "scrooge" } } as any,
      config: {} as any,
    });
    expect(result).not.toBeNull();
    expect(result?.name).toBe("cost_alarm");
  });

  it("returns null when LLM says triggered=false", async () => {
    const engine = new SkillsEngine();
    const skill = makeBehavioralSkill();
    const provider = makeProvider(
      '{"triggered": false, "skillId": null}',
    );
    const result = await engine.evaluate("tell me a joke", [skill], {
      provider,
      owl: { persona: { name: "scrooge" } } as any,
      config: {} as any,
    });
    expect(result).toBeNull();
  });

  it("returns null and does not throw on malformed LLM JSON", async () => {
    const engine = new SkillsEngine();
    const skill = makeBehavioralSkill();
    const provider = makeProvider("not json at all");
    const result = await engine.evaluate("billing question", [skill], {
      provider,
      owl: { persona: { name: "scrooge" } } as any,
      config: {} as any,
    });
    expect(result).toBeNull();
  });

  it("strips markdown code fences from LLM response", async () => {
    const engine = new SkillsEngine();
    const skill = makeBehavioralSkill();
    const provider = makeProvider(
      '```json\n{"triggered": true, "skillId": "cost_alarm"}\n```',
    );
    const result = await engine.evaluate("cloud costs question", [skill], {
      provider,
      owl: { persona: { name: "scrooge" } } as any,
      config: {} as any,
    });
    expect(result?.name).toBe("cost_alarm");
  });
});
