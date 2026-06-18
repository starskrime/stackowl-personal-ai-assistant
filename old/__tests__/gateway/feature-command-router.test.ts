import { describe, it, expect, vi } from "vitest";
import { FeatureCommandRouter } from "../../src/gateway/feature-command-router.js";
import type { IFeatureCommandHandler, FeatureCommandContext } from "../../src/gateway/feature-command-router.js";
import type { GatewayResponse } from "../../src/gateway/types.js";

const makeCtx = (): FeatureCommandContext =>
  ({ session: { id: "s1", messages: [], owlName: "owl", createdAt: 0, updatedAt: 0 } } as any);

const makeHandler = (commands: string[], response: string): IFeatureCommandHandler => ({
  commands,
  handle: vi.fn().mockResolvedValue({
    content: response,
    owlName: "owl",
    owlEmoji: "🦉",
    toolsUsed: [],
  } as GatewayResponse),
});

describe("FeatureCommandRouter", () => {
  it("dispatches to registered handler", async () => {
    const router = new FeatureCommandRouter();
    const handler = makeHandler(["/trust"], "trust status");
    router.register(handler);
    const result = await router.dispatch("/trust", makeCtx());
    expect(result?.content).toBe("trust status");
    expect(handler.handle).toHaveBeenCalledWith("/trust", [], expect.any(Object));
  });

  it("returns null for unknown command", async () => {
    const router = new FeatureCommandRouter();
    const result = await router.dispatch("/unknown", makeCtx());
    expect(result).toBeNull();
  });

  it("returns null for non-command input", async () => {
    const router = new FeatureCommandRouter();
    const result = await router.dispatch("hello world", makeCtx());
    expect(result).toBeNull();
  });

  it("isCommand returns true for registered command", () => {
    const router = new FeatureCommandRouter();
    router.register(makeHandler(["/foo"], "bar"));
    expect(router.isCommand("/foo")).toBe(true);
  });

  it("isCommand returns false for unregistered command", () => {
    const router = new FeatureCommandRouter();
    expect(router.isCommand("/nope")).toBe(false);
  });

  it("parses args correctly", async () => {
    const router = new FeatureCommandRouter();
    const handler = makeHandler(["/fork"], "forked");
    router.register(handler);
    await router.dispatch("/fork my reason here", makeCtx());
    expect(handler.handle).toHaveBeenCalledWith("/fork", ["my", "reason", "here"], expect.any(Object));
  });

  it("handler registered for multiple commands routes both", async () => {
    const router = new FeatureCommandRouter();
    const handler = makeHandler(["/pellet", "/pellets"], "pellet list");
    router.register(handler);
    const r1 = await router.dispatch("/pellet", makeCtx());
    const r2 = await router.dispatch("/pellets", makeCtx());
    expect(r1?.content).toBe("pellet list");
    expect(r2?.content).toBe("pellet list");
  });
});
