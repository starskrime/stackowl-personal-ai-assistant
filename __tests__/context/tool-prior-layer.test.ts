/**
 * StackOwl — Element 7 T12 — ToolPriorLayer
 *
 * Wraps PersonalizedRouter as a ContextPipeline layer that emits a short
 * "Tools that worked well on similar past requests: ..." nudge when the
 * router returns suggestions. Empty string on cold-start (router → []),
 * skipped on conversational turns where tool selection is irrelevant.
 */
import { describe, it, expect } from "vitest";
import { ToolPriorLayer } from "../../src/context/layers/tool-prior.js";

const baseReq = {
  session: { messages: [] },
  callbacks: {},
  continuityResult: null,
  digest: null,
  deps: { sessionStore: {}, config: {} },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

const baseTriage = {
  userMessage: "look up the typescript 5.5 release notes",
  isConversational: false,
  hasFrustration: false,
  isOpinionRequest: false,
  hasTemporalTrigger: false,
  isReturningUser: false,
  sessionDepth: 1,
  hasActiveItems: false,
  effectiveUserId: "u1",
  continuityClass: null,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

describe("ToolPriorLayer", () => {
  it("emits a nudge when the router returns suggestions", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const router = { suggestTools: async () => ["web", "document"] } as any;
    const layer = new ToolPriorLayer(router);
    const out = await layer.build(baseReq, baseTriage, new Map());
    expect(out).toContain("web");
    expect(out).toContain("document");
    expect(out).toMatch(/similar past/i);
  });

  it("returns empty string when the router returns []", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const router = { suggestTools: async () => [] } as any;
    const layer = new ToolPriorLayer(router);
    expect(await layer.build(baseReq, baseTriage, new Map())).toBe("");
  });

  it("returns empty string when no userMessage is present", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const router = { suggestTools: async () => ["web"] } as any;
    const layer = new ToolPriorLayer(router);
    const out = await layer.build(
      baseReq,
      { ...baseTriage, userMessage: "" },
      new Map(),
    );
    expect(out).toBe("");
  });

  it("shouldFire returns false for conversational turns", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const router = { suggestTools: async () => [] } as any;
    const layer = new ToolPriorLayer(router);
    expect(layer.shouldFire({ ...baseTriage, isConversational: true })).toBe(
      false,
    );
    expect(layer.shouldFire(baseTriage)).toBe(true);
  });

  it("caps the visible suggestions at 5", async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const router = {
      suggestTools: async () => [
        "web",
        "document",
        "memory",
        "sandbox",
        "vision",
        "extra-1",
        "extra-2",
      ],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any;
    const layer = new ToolPriorLayer(router);
    const out = await layer.build(baseReq, baseTriage, new Map());
    expect(out).toContain("vision");
    expect(out).not.toContain("extra-1");
    expect(out).not.toContain("extra-2");
  });
});
