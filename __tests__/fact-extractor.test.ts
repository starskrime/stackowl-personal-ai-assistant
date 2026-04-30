import { describe, it, expect, vi } from "vitest";
import { extractFactsFromConversation } from "../src/session/fact-extractor.js";

// Mock provider that returns a given JSON string via chat()
function makeMockProvider(responseJson: string) {
  return {
    chat: vi.fn().mockResolvedValue({ content: responseJson, model: "test", finishReason: "stop" }),
  };
}

// Mock ProviderRegistry — get() returns provider or throws if undefined passed
function makeMockRegistry(provider: ReturnType<typeof makeMockProvider> | undefined) {
  return {
    get: vi.fn().mockImplementation(() => {
      if (!provider) throw new Error("Provider not found");
      return provider;
    }),
  };
}

describe("extractFactsFromConversation", () => {
  const messages = [
    { role: "user" as const, content: "I prefer TypeScript" },
    { role: "assistant" as const, content: "Noted!" },
    { role: "user" as const, content: "I've been coding for 10 years" },
  ];

  it("returns extracted facts from valid LLM response", async () => {
    const json = JSON.stringify([
      { fact: "Prefers TypeScript", category: "preference" },
      { fact: "10 years coding experience", category: "skill" },
    ]);
    const provider = makeMockProvider(json);
    const registry = makeMockRegistry(provider);

    const facts = await extractFactsFromConversation(
      messages,
      undefined,
      registry as any,
      "openai",
      "gpt-4o-mini",
    );

    expect(facts).toHaveLength(2);
    expect(facts[0].fact).toBe("Prefers TypeScript");
    expect(facts[0].category).toBe("preference");
  });

  it("returns empty array on malformed JSON", async () => {
    const provider = makeMockProvider("not json at all");
    const registry = makeMockRegistry(provider);
    const facts = await extractFactsFromConversation(messages, undefined, registry as any, "openai", "gpt-4o-mini");
    expect(facts).toEqual([]);
  });

  it("filters out facts with invalid categories", async () => {
    const json = JSON.stringify([
      { fact: "Likes cats", category: "episode" },  // invalid
      { fact: "Expert in Go", category: "skill" },   // valid
    ]);
    const provider = makeMockProvider(json);
    const registry = makeMockRegistry(provider);
    const facts = await extractFactsFromConversation(messages, undefined, registry as any, "openai", "gpt-4o-mini");
    expect(facts).toHaveLength(1);
    expect(facts[0].category).toBe("skill");
  });

  it("caps output at 10 facts", async () => {
    const json = JSON.stringify(
      Array.from({ length: 15 }, (_, i) => ({ fact: `fact ${i}`, category: "preference" }))
    );
    const provider = makeMockProvider(json);
    const registry = makeMockRegistry(provider);
    const facts = await extractFactsFromConversation(messages, undefined, registry as any, "openai", "gpt-4o-mini");
    expect(facts).toHaveLength(10);
  });

  it("returns empty array when provider not found", async () => {
    const registry = makeMockRegistry(undefined);
    const facts = await extractFactsFromConversation(messages, undefined, registry as any, "missing", "model");
    expect(facts).toEqual([]);
  });
});
