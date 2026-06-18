// __tests__/secretary-signals.test.ts
import { describe, it, expect } from "vitest";
import { SecretaryRouter } from "../src/routing/secretary.js";
import type { RoutingSignals } from "../src/routing/user-profile-service.js";

const noSignals: RoutingSignals = { activePin: null, domainStack: [], recentEpisodes: [], relevantFacts: [], trustLevel: "standard" };

function makeRegistry(specialists: { name: string; role: string; expertise: string[]; routingRules: any; personality: any; permissions: any }[]) {
  return {
    listSpecialists: () => specialists,
    get: (name: string) => specialists.find(s => s.name === name),
    getDefault: () => null,
  } as any;
}

const tsOwl = {
  name: "typescript-owl", role: "TypeScript expert", expertise: ["TypeScript", "Node.js"],
  routingRules: { keywords: ["typescript", "ts", "node"] },
  personality: { challengeLevel: "medium", verbosity: "concise", tone: "technical" },
  permissions: { capabilityConstraints: [] },
};
const rustOwl = {
  name: "rust-owl", role: "Rust expert", expertise: ["Rust", "systems programming"],
  routingRules: { keywords: ["rust", "cargo", "borrow"] },
  personality: { challengeLevel: "medium", verbosity: "concise", tone: "technical" },
  permissions: { capabilityConstraints: [] },
};

describe("SecretaryRouter.routeWithSignals", () => {
  it("routes to direct when no specialists", async () => {
    const router = new SecretaryRouter(makeRegistry([]), undefined);
    const result = await router.routeWithSignals("hello", "u1", noSignals);
    expect(result.type).toBe("direct");
  });

  it("domain signal boosts correct specialist", async () => {
    const router = new SecretaryRouter(makeRegistry([tsOwl, rustOwl]), undefined);
    const signals: RoutingSignals = { ...noSignals, domainStack: ["Build TypeScript API", "Write TypeScript tests"] };
    const result = await router.routeWithSignals("help me with my project", "u1", signals);
    expect(result.type).toBe("specialist");
    if (result.type === "specialist") expect(result.owl.name).toBe("typescript-owl");
  });

  it("fact signal boosts specialist mentioned by name", async () => {
    const router = new SecretaryRouter(makeRegistry([tsOwl, rustOwl]), undefined);
    const signals: RoutingSignals = { ...noSignals, relevantFacts: ["user uses rust-owl for systems work"] };
    const result = await router.routeWithSignals("optimize memory allocations", "u1", signals);
    expect(result.type).toBe("specialist");
    if (result.type === "specialist") expect(result.owl.name).toBe("rust-owl");
  });

  it("keyword match still works without signals", async () => {
    const router = new SecretaryRouter(makeRegistry([tsOwl, rustOwl]), undefined);
    const result = await router.routeWithSignals("help me with typescript interfaces", "u1", noSignals);
    expect(result.type).toBe("specialist");
    if (result.type === "specialist") expect(result.owl.name).toBe("typescript-owl");
  });
});
