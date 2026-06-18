import { describe, it, expect, vi } from "vitest";
import { InstinctEngine } from "../../src/instincts/engine.js";
import { InstinctRegistry } from "../../src/instincts/registry.js";
import type { ModelProvider, ChatResponse } from "../../src/providers/base.js";
import type { InstinctSpec } from "../../src/instincts/types.js";

function makeRegistry(specs: Omit<InstinctSpec, "owlName">[], owlName = "noctua"): InstinctRegistry {
  const registry = new InstinctRegistry();
  const full = specs.map((s) => ({ ...s, owlName }));
  // Directly populate cache via get — bypass file I/O
  (registry as unknown as { cache: Map<string, InstinctSpec[]> }).cache.set(owlName, full);
  return registry;
}

function makeProvider(reply: string): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({
      content: reply,
      model: "mock",
      finishReason: "stop",
    } satisfies ChatResponse),
  } as unknown as ModelProvider;
}

const SPEC_A: Omit<InstinctSpec, "owlName"> = {
  name: "be-concise",
  description: "user wants a short answer",
  constraint: "Keep reply under 3 sentences.",
};

const SPEC_B: Omit<InstinctSpec, "owlName"> = {
  name: "use-examples",
  description: "user asks for an example",
  constraint: "Always include a concrete example.",
};

describe("InstinctEngine.evaluate", () => {
  it("returns empty array when registry has no instincts", async () => {
    const registry = makeRegistry([]);
    const engine = new InstinctEngine(makeProvider("[0]"), "m", registry);
    expect(await engine.evaluate("noctua", "hello")).toEqual([]);
  });

  it("returns matching instinct by LLM index", async () => {
    const registry = makeRegistry([SPEC_A, SPEC_B]);
    const engine = new InstinctEngine(makeProvider("[0]"), "m", registry);
    const result = await engine.evaluate("noctua", "be brief");
    expect(result).toHaveLength(1);
    expect(result[0].name).toBe("be-concise");
  });

  it("returns multiple instincts when LLM returns multiple indices", async () => {
    const registry = makeRegistry([SPEC_A, SPEC_B]);
    const engine = new InstinctEngine(makeProvider("[0,1]"), "m", registry);
    const result = await engine.evaluate("noctua", "short example please");
    expect(result).toHaveLength(2);
  });

  it("returns empty array when LLM returns []", async () => {
    const registry = makeRegistry([SPEC_A]);
    const engine = new InstinctEngine(makeProvider("[]"), "m", registry);
    expect(await engine.evaluate("noctua", "hello")).toEqual([]);
  });

  it("ignores out-of-range indices from LLM", async () => {
    const registry = makeRegistry([SPEC_A]);
    const engine = new InstinctEngine(makeProvider("[0,5,99]"), "m", registry);
    const result = await engine.evaluate("noctua", "test");
    expect(result).toHaveLength(1);
  });

  it("returns empty array when LLM returns malformed JSON", async () => {
    const registry = makeRegistry([SPEC_A]);
    const engine = new InstinctEngine(makeProvider("sure, I'd pick 0"), "m", registry);
    const result = await engine.evaluate("noctua", "test");
    expect(result).toEqual([]);
  });

  it("returns empty array when provider throws", async () => {
    const registry = makeRegistry([SPEC_A]);
    const provider = {
      name: "mock",
      chat: vi.fn().mockRejectedValue(new Error("network error")),
    } as unknown as ModelProvider;
    const engine = new InstinctEngine(provider, "m", registry);
    expect(await engine.evaluate("noctua", "test")).toEqual([]);
  });
});

describe("InstinctEngine.buildConstraintBlock", () => {
  it("returns empty string for empty array", () => {
    expect(InstinctEngine.buildConstraintBlock([])).toBe("");
  });

  it("builds block with constraint lines", () => {
    const instincts: InstinctSpec[] = [
      { name: "a", description: "d", constraint: "Be brief.", owlName: "noctua" },
      { name: "b", description: "d", constraint: "Use examples.", owlName: "noctua" },
    ];
    const block = InstinctEngine.buildConstraintBlock(instincts);
    expect(block).toContain("[Active instincts]");
    expect(block).toContain("- Be brief.");
    expect(block).toContain("- Use examples.");
  });
});
