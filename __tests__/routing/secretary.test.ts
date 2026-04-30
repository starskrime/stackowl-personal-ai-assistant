import { describe, it, expect } from "vitest";
import { SpecializedOwlRegistry } from "../../src/owls/specialized-registry.js";
import { SecretaryRouter } from "../../src/routing/secretary.js";

function makeRegistry(specs: Array<{ name: string; role: string; expertise?: string[]; keywords?: string[] }>): SpecializedOwlRegistry {
  const registry = new SpecializedOwlRegistry();
  (registry as any).specs = new Map(
    specs.map((s) => [
      s.name.toLowerCase(),
      {
        name: s.name,
        type: "specialist" as const,
        role: s.role,
        emoji: "🦉",
        expertise: s.expertise ?? [],
        personality: { challengeLevel: "medium" as const, verbosity: "balanced" as const, tone: "neutral" },
        model: { provider: "", model: "" },
        permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
        routingRules: { keywords: s.keywords ?? [] },
        skills: { allowed: [] },
        additionalPrompt: "",
      },
    ]),
  );
  return registry;
}

describe("SecretaryRouter", () => {
  describe("route() — no specialists", () => {
    it("returns direct immediately when registry is empty", async () => {
      const router = new SecretaryRouter(makeRegistry([]));

      const decision = await router.route("Hello", "user_test");

      expect(decision.type).toBe("direct");
      expect(decision.reason).toBe("No specialized owls configured");
    });
  });

  describe("route() — keyword routing", () => {
    it("routes to specialist when keywords match and confidence is sufficient", async () => {
      const registry = makeRegistry([{ name: "TradingBot", role: "trading assistant", keywords: ["stock", "trade", "portfolio"] }]);
      const router = new SecretaryRouter(registry);

      const decision = await router.route("I want to buy stocks", "user_test");

      expect(decision.type).toBe("specialist");
      if (decision.type === "specialist") {
        expect(decision.owl.name).toBe("TradingBot");
      }
    });

    it("returns direct when no keywords match the message", async () => {
      const registry = makeRegistry([{ name: "TradingBot", role: "trading assistant", keywords: ["stock", "trade"] }]);
      const router = new SecretaryRouter(registry);

      const decision = await router.route("What is the weather?", "user_test");

      expect(decision.type).toBe("direct");
    });

    it("routes to parliament when message triggers parliament keywords", async () => {
      const registry = makeRegistry([{ name: "SomeOwl", role: "assistant" }]);
      const router = new SecretaryRouter(registry);

      const decision = await router.route(
        "Compare two programming languages: analyze the advantages and disadvantages, then evaluate the strategy for choosing one?",
        "user_test",
      );

      expect(decision.type).toBe("parliament");
    });

    it("returns direct when message is too short to match specialist", async () => {
      const registry = makeRegistry([{ name: "TradingBot", role: "trading assistant", keywords: ["stock", "trade"] }]);
      const router = new SecretaryRouter(registry);

      const decision = await router.route("stock", "user_test");

      expect(decision.type).toBe("direct");
    });
  });

  describe("route() — keyword fallback (no classify fn)", () => {
    it("routes to specialist whose keywords match the message", async () => {
      const registry = makeRegistry([{ name: "TradingBot", role: "trading", keywords: ["stock", "trade", "portfolio"] }]);
      const router = new SecretaryRouter(registry);

      const decision = await router.route("I want to buy some stocks", "user_test");

      expect(decision.type).toBe("specialist");
      if (decision.type === "specialist") {
        expect(decision.owl.name).toBe("TradingBot");
      }
    });

    it("returns direct when no keywords match", async () => {
      const registry = makeRegistry([{ name: "TradingBot", role: "trading", keywords: ["stock", "trade"] }]);
      const router = new SecretaryRouter(registry);

      const decision = await router.route("Tell me a joke", "user_test");

      expect(decision.type).toBe("direct");
    });
  });
});
