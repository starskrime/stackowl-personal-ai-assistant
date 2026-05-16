import { describe, it, expect, vi, beforeEach } from "vitest";
import { handleConfigProvider } from "../../../../../src/cli/v2/commands/handlers/config/provider.js";
import type { CommandContext } from "../../../../../src/cli/v2/commands/registry.js";
import type { StackOwlConfig } from "../../../../../src/config/loader.js";

vi.mock("../../../../../src/config/patch.js", () => ({
  patchConfig: vi.fn().mockResolvedValue({ hotReloaded: false, restartRequired: true }),
}));

function mkConfig(overrides: Partial<StackOwlConfig> = {}): StackOwlConfig {
  return {
    providers: { ollama: { baseUrl: "http://127.0.0.1:11434", activeModel: "llama3.2" } },
    defaultProvider: "ollama",
    defaultModel: "llama3.2",
    workspace: "./workspace",
    gateway: { port: 3077, host: "127.0.0.1" },
    parliament: { maxRounds: 3, maxOwls: 6 },
    heartbeat: { enabled: false, intervalMinutes: 30 },
    owlDna: { enabled: true, evolutionBatchSize: 5, decayRatePerWeek: 0.1 },
    ...overrides,
  };
}

function mkCtx(cfg: StackOwlConfig = mkConfig()): CommandContext {
  return {
    getOwlGateway: () => ({
      getConfig: () => cfg,
      getWorkspacePath: () => "/tmp/test",
      getProviderManager: () => ({
        testProvider: vi.fn().mockResolvedValue({ ok: true, latencyMs: 42 }),
      }),
    }),
    bridge: { emit: vi.fn() },
    getStore: vi.fn(),
    getMemoryRepo: vi.fn(),
    getMcpManager: vi.fn(),
  } as unknown as CommandContext;
}

describe("/config provider", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows error for unknown verb", async () => {
    const result = await handleConfigProvider(mkCtx(), ["bogus"]);
    expect(result.kind).toBe("error");
  });

  it("list returns system-message with provider info", async () => {
    const result = await handleConfigProvider(mkCtx(), ["list"]);
    expect(result.kind).toBe("system-message");
    expect((result as { kind: "system-message"; text: string }).text).toContain("ollama");
  });

  it("list masks api keys", async () => {
    const cfg = mkConfig();
    cfg.providers["ollama"]!.apiKey = "sk-secret-key-1234";
    const result = await handleConfigProvider(mkCtx(cfg), ["list"]);
    expect((result as { kind: "system-message"; text: string }).text).not.toContain("sk-secret-key-1234");
    expect((result as { kind: "system-message"; text: string }).text).toContain("1234");
  });

  it("add returns error if provider already exists", async () => {
    const result = await handleConfigProvider(mkCtx(), ["add", "ollama"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("already exists");
  });

  it("add requires a name", async () => {
    const result = await handleConfigProvider(mkCtx(), ["add"]);
    expect(result.kind).toBe("error");
  });

  it("remove requires --confirm", async () => {
    const result = await handleConfigProvider(mkCtx(), ["remove", "ollama"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("--confirm");
  });

  it("remove rejects removing the default provider", async () => {
    const result = await handleConfigProvider(mkCtx(), ["remove", "ollama", "--confirm"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("default provider");
  });

  it("set-key requires name and key", async () => {
    const result = await handleConfigProvider(mkCtx(), ["set-key", "ollama"]);
    expect(result.kind).toBe("error");
  });

  it("set-key returns error for unknown provider", async () => {
    const result = await handleConfigProvider(mkCtx(), ["set-key", "unknown", "sk-123"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("not found");
  });

  it("set-model requires name and model", async () => {
    const result = await handleConfigProvider(mkCtx(), ["set-model", "ollama"]);
    expect(result.kind).toBe("error");
  });

  it("set-url rejects non-http URLs", async () => {
    const result = await handleConfigProvider(mkCtx(), ["set-url", "ollama", "ftp://bad"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("http");
  });

  it("test calls providerManager.testProvider and returns result", async () => {
    const result = await handleConfigProvider(mkCtx(), ["test", "ollama"]);
    expect(result.kind).toBe("system-message");
    expect((result as { kind: "system-message"; text: string }).text).toContain("✅");
  });

  it("test requires a name", async () => {
    const result = await handleConfigProvider(mkCtx(), ["test"]);
    expect(result.kind).toBe("error");
  });
});
