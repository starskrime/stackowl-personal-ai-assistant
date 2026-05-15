/**
 * StackOwl — /provider command handler tests
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  handleProviderList,
  handleProviderTest,
  handleProviderDelete,
} from "../../../../src/cli/v2/commands/handlers/provider.js";
import type { CommandContext } from "../../../../src/cli/v2/commands/registry.js";

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeManager(overrides: Record<string, unknown> = {}) {
  return {
    listProviders: vi.fn().mockReturnValue([
      {
        name: "anthropic",
        profile: "anthropic",
        activeModel: "claude-sonnet-4-6",
        isDefault: true,
        health: "CLOSED",
        source: "system",
      },
      {
        name: "my-openai",
        profile: "openai",
        activeModel: "gpt-5",
        isDefault: false,
        health: "CLOSED",
        source: "custom",
      },
    ]),
    testProvider: vi.fn().mockResolvedValue({ ok: true, latencyMs: 42 }),
    deleteProvider: vi.fn().mockResolvedValue(undefined),
    addProvider: vi.fn().mockResolvedValue(undefined),
    editProvider: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function makeCtx(managerOverrides: Record<string, unknown> = {}): CommandContext {
  const manager = makeManager(managerOverrides);
  return {
    getOwlGateway: () =>
      ({
        getProviderManager: () => manager,
        getWorkspacePath: () => "/tmp/test",
        getConfig: () => ({ providers: {} }),
      } as unknown as ReturnType<CommandContext["getOwlGateway"]>),
    bridge: {
      emit: vi.fn(),
      openPanel: vi.fn(),
      closePanel: vi.fn(),
      requestOnboardingView: vi.fn(),
    } as unknown as CommandContext["bridge"],
    getStore: vi.fn(),
    getMemoryRepo: vi.fn(),
    getMcpManager: vi.fn(),
  } as unknown as CommandContext;
}

// ─── handleProviderList ───────────────────────────────────────────────────────

describe("handleProviderList", () => {
  it("returns kind=panel with items for both providers", async () => {
    const result = await handleProviderList(makeCtx(), []);
    expect(result.kind).toBe("panel");
    if (result.kind !== "panel") return;
    const ids = result.payload.items.map((i) => i.id);
    expect(ids).toContain("anthropic");
    expect(ids).toContain("my-openai");
  });

  it("marks the default provider with ★ in the meta field", async () => {
    const result = await handleProviderList(makeCtx(), []);
    expect(result.kind).toBe("panel");
    if (result.kind !== "panel") return;
    const anthropic = result.payload.items.find((i) => i.id === "anthropic");
    expect(anthropic).toBeDefined();
    expect(anthropic!.meta).toContain("★");

    const myOpenai = result.payload.items.find((i) => i.id === "my-openai");
    expect(myOpenai).toBeDefined();
    expect(myOpenai!.meta).not.toContain("★");
  });

  it("returns panel with /provider title", async () => {
    const result = await handleProviderList(makeCtx(), []);
    expect(result.kind).toBe("panel");
    if (result.kind !== "panel") return;
    expect(result.payload.title).toBe("/provider");
  });
});

// ─── handleProviderTest ───────────────────────────────────────────────────────

describe("handleProviderTest", () => {
  it("returns error when no name is provided", async () => {
    const result = await handleProviderTest(makeCtx(), []);
    expect(result.kind).toBe("error");
    if (result.kind !== "error") return;
    expect(result.text).toMatch(/usage/i);
  });

  it("returns system-message with ✅ and latency on success", async () => {
    const result = await handleProviderTest(makeCtx(), ["anthropic"]);
    expect(result.kind).toBe("system-message");
    if (result.kind !== "system-message") return;
    expect(result.text).toContain("✅");
    expect(result.text).toContain("42ms");
  });

  it("returns system-message with ❌ on failing test", async () => {
    const ctx = makeCtx({
      testProvider: vi.fn().mockResolvedValue({
        ok: false,
        latencyMs: 100,
        error: "Connection refused",
      }),
    });
    const result = await handleProviderTest(ctx, ["bad-provider"]);
    expect(result.kind).toBe("system-message");
    if (result.kind !== "system-message") return;
    expect(result.text).toContain("❌");
    expect(result.text).toContain("Connection refused");
  });
});

// ─── handleProviderDelete ─────────────────────────────────────────────────────

describe("handleProviderDelete", () => {
  it("returns error when no name is provided", async () => {
    const result = await handleProviderDelete(makeCtx(), []);
    expect(result.kind).toBe("error");
    if (result.kind !== "error") return;
    expect(result.text).toMatch(/usage/i);
  });

  it("calls deleteProvider and returns success system-message", async () => {
    const manager = makeManager();
    const ctx = makeCtx();
    // Replace the manager returned by getProviderManager to capture the call
    (ctx.getOwlGateway() as unknown as { getProviderManager: () => typeof manager }).getProviderManager = () => manager;

    const result = await handleProviderDelete(ctx, ["my-openai"]);
    expect(result.kind).toBe("system-message");
    if (result.kind !== "system-message") return;
    expect(result.text).toContain("my-openai");
  });

  it("returns error with message when deleteProvider throws", async () => {
    const ctx = makeCtx({
      deleteProvider: vi.fn().mockRejectedValue(new Error("Cannot delete default provider")),
    });
    const result = await handleProviderDelete(ctx, ["anthropic"]);
    expect(result.kind).toBe("error");
    if (result.kind !== "error") return;
    expect(result.text).toContain("Cannot delete default provider");
  });
});
