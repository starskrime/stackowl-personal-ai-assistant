import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdirSync, rmSync } from "node:fs";
import { ProviderManager } from "../../src/providers/manager.js";
import { ProviderRegistry } from "../../src/providers/registry.js";
import type { StackOwlConfig } from "../../src/config/loader.js";
import type { ModelProvider } from "../../src/providers/base.js";
import { initModelLoader, resetModelLoader } from "../../src/models/loader.js";

function makeProvider(name = "test"): ModelProvider {
  return {
    name,
    chat: vi.fn(),
    chatWithTools: vi.fn(),
    chatStream: vi.fn(),
    embed: vi.fn(),
    listModels: vi.fn().mockResolvedValue([]),
    healthCheck: vi.fn().mockResolvedValue(true),
  } as unknown as ModelProvider;
}

function makeConfig(overrides: Partial<StackOwlConfig> = {}): StackOwlConfig {
  return {
    providers: { anthropic: { apiKey: "sk-ant-existing", profile: "anthropic" } },
    defaultProvider: "anthropic",
    defaultModel: "claude-sonnet-4-6",
    workspace: "./workspace",
    gateway: { port: 3077, host: "127.0.0.1", outputMode: "normal" },
    parliament: { maxRounds: 3, maxOwls: 6 },
    heartbeat: { enabled: false, intervalMinutes: 30 },
    owlDna: { enabled: true, evolutionBatchSize: 5, decayRatePerWeek: 0.1 },
    ...overrides,
  } as StackOwlConfig;
}

describe("ProviderManager", () => {
  let tmpDir: string;
  let registry: ProviderRegistry;
  let config: StackOwlConfig;
  let saveFn: ReturnType<typeof vi.fn>;
  let manager: ProviderManager;

  beforeEach(() => {
    tmpDir = join(
      tmpdir(),
      `pm-test-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    );
    mkdirSync(tmpDir, { recursive: true });
    resetModelLoader();
    initModelLoader(); // system models only

    registry = new ProviderRegistry();
    registry._registerForTest("anthropic", makeProvider("anthropic"));
    (registry as any).defaultProviderName = "anthropic";

    config = makeConfig();
    saveFn = vi.fn().mockResolvedValue(undefined);
    manager = new ProviderManager(registry, config, tmpDir, saveFn);
  });

  afterEach(() => {
    try {
      rmSync(tmpDir, { recursive: true });
    } catch {
      /* ok */
    }
    resetModelLoader();
  });

  // ── addProvider ──────────────────────────────────────────────────

  it("addProvider: throws on reserved system name", async () => {
    await expect(
      manager.addProvider({
        name: "anthropic",
        profile: "anthropic",
        apiKey: "sk-ant-123",
      }),
    ).rejects.toThrow(/reserved/i);
  });

  it("addProvider: throws when name already exists in config", async () => {
    // "anthropic" is already in config.providers from makeConfig()
    await expect(
      manager.addProvider({
        name: "anthropic",
        profile: "anthropic",
        apiKey: "sk-ant-123",
      }),
    ).rejects.toThrow();
  });

  it("addProvider: writes config entry and calls saveFn", async () => {
    await manager.addProvider({
      name: "my-openai",
      profile: "openai",
      apiKey: "sk-123",
      activeModel: "gpt-5",
    });
    expect(config.providers["my-openai"]).toMatchObject({
      profile: "openai",
      apiKey: "sk-123",
      activeModel: "gpt-5",
    });
    expect(saveFn).toHaveBeenCalledOnce();
  });

  it("addProvider: throws on invalid name characters", async () => {
    await expect(
      manager.addProvider({
        name: "my_provider!",
        profile: "openai",
        apiKey: "sk-123",
      }),
    ).rejects.toThrow(/invalid.*name/i);
  });

  it("addProvider: succeeds even when hot-register fails (config still saved)", async () => {
    // Make registry.register throw
    vi.spyOn(registry, "register").mockImplementationOnce(() => {
      throw new Error("protocol not found");
    });
    // Should not throw — hot-register failure is non-fatal
    await expect(
      manager.addProvider({ name: "my-openai", profile: "openai", apiKey: "sk-123" }),
    ).resolves.not.toThrow();
    // Config was still saved
    expect(config.providers["my-openai"]).toBeDefined();
    expect(saveFn).toHaveBeenCalledOnce();
  });

  // ── editProvider ─────────────────────────────────────────────────

  it("editProvider: updates apiKey in config and saves", async () => {
    await manager.editProvider("anthropic", { apiKey: "sk-ant-new-key" });
    expect(config.providers["anthropic"]?.apiKey).toBe("sk-ant-new-key");
    expect(saveFn).toHaveBeenCalledOnce();
  });

  it("editProvider: throws for unknown provider name", async () => {
    await expect(
      manager.editProvider("no-such", { apiKey: "x" }),
    ).rejects.toThrow(/not found/i);
  });

  it("editProvider: deregisters before re-registering with new config", async () => {
    const deregisterSpy = vi.spyOn(registry, "deregister");
    const registerSpy = vi.spyOn(registry, "register");
    await manager.editProvider("anthropic", { apiKey: "sk-ant-new-key" });
    expect(deregisterSpy).toHaveBeenCalledWith("anthropic");
    expect(registerSpy).toHaveBeenCalled();
    // Deregister was called before register
    expect(deregisterSpy.mock.invocationCallOrder[0]).toBeLessThan(
      registerSpy.mock.invocationCallOrder[0]!,
    );
  });

  // ── deleteProvider ───────────────────────────────────────────────

  it("deleteProvider: throws when trying to delete the default provider", async () => {
    await expect(manager.deleteProvider("anthropic")).rejects.toThrow(
      /default/i,
    );
  });

  it("deleteProvider: removes provider from config and saves", async () => {
    config.providers["my-openai"] = { profile: "openai", apiKey: "sk-123" };
    registry._registerForTest("my-openai", makeProvider("my-openai"));
    await manager.deleteProvider("my-openai");
    expect(config.providers["my-openai"]).toBeUndefined();
    expect(saveFn).toHaveBeenCalledOnce();
  });

  it("deleteProvider: calls registry.deregister", async () => {
    config.providers["my-openai"] = { profile: "openai", apiKey: "sk-123" };
    registry._registerForTest("my-openai", makeProvider("my-openai"));
    await manager.deleteProvider("my-openai");
    expect(() => registry.get("my-openai")).toThrow();
  });

  // ── listProviders ────────────────────────────────────────────────

  it("listProviders: returns all config providers with status", () => {
    const statuses = manager.listProviders();
    expect(statuses).toHaveLength(1);
    expect(statuses[0]).toMatchObject({
      name: "anthropic",
      isDefault: true,
    });
  });

  // ── testProvider ─────────────────────────────────────────────────

  it("testProvider: returns ok:true when healthCheck resolves true", async () => {
    const result = await manager.testProvider("anthropic");
    expect(result.ok).toBe(true);
    expect(result.latencyMs).toBeGreaterThanOrEqual(0);
  });

  it("testProvider: returns ok:false with error message when healthCheck throws", async () => {
    const failProvider = makeProvider("fail");
    (
      failProvider.healthCheck as ReturnType<typeof vi.fn>
    ).mockRejectedValue(new Error("connection refused"));
    registry._registerForTest("fail-prov", failProvider);
    config.providers["fail-prov"] = { profile: "openai" };
    const result = await manager.testProvider("fail-prov");
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/connection refused/i);
  });

  // ── isReservedOrDuplicate ────────────────────────────────────────

  it("isReservedOrDuplicate: returns true for system name", () => {
    // "anthropic" is a system model name
    expect(manager.isReservedOrDuplicate("anthropic", config)).toBe(true);
  });

  it("isReservedOrDuplicate: returns true for existing config entry", () => {
    // "anthropic" exists in config.providers
    expect(manager.isReservedOrDuplicate("anthropic", config)).toBe(true);
  });

  it("isReservedOrDuplicate: returns false for a new unique name", () => {
    expect(manager.isReservedOrDuplicate("brand-new-provider", config)).toBe(false);
  });
});
