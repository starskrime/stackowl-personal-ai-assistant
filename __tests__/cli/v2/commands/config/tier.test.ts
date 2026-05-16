import { describe, it, expect, vi, beforeEach } from "vitest";
import { handleConfigTier } from "../../../../../src/cli/v2/commands/handlers/config/tier.js";
import type { CommandContext } from "../../../../../src/cli/v2/commands/registry.js";
import type { StackOwlConfig } from "../../../../../src/config/loader.js";

vi.mock("../../../../../src/config/patch.js", () => ({
  patchConfig: vi.fn().mockResolvedValue({ hotReloaded: true, restartRequired: false }),
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
    }),
    bridge: { emit: vi.fn() },
    getStore: vi.fn(),
    getMemoryRepo: vi.fn(),
    getMcpManager: vi.fn(),
  } as unknown as CommandContext;
}

describe("/config tier", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows error for unknown verb", async () => {
    const result = await handleConfigTier(mkCtx(), ["bogus"]);
    expect(result.kind).toBe("error");
  });

  it("list shows (none configured) when no intelligence config", async () => {
    const result = await handleConfigTier(mkCtx(), ["list"]);
    expect(result.kind).toBe("system-message");
    expect((result as { kind: "system-message"; text: string }).text).toContain("none configured");
  });

  it("list shows tier config when intelligence is set", async () => {
    const cfg = mkConfig({
      intelligence: {
        tiers: {
          low: { provider: "ollama", model: "llama3.2" },
          mid: { provider: "anthropic", model: "claude-sonnet-4-6" },
          high: { provider: "anthropic", model: "claude-opus-4-7" },
        },
        defaults: { conversation: "low", parliament: "mid", evolution: "high",
          extraction: "low", episodic: "low", classification: "low",
          synthesis: "low", summarization: "low", clarification: "low" },
      },
    });
    const result = await handleConfigTier(mkCtx(cfg), ["list"]);
    expect(result.kind).toBe("system-message");
    const text = (result as { kind: "system-message"; text: string }).text;
    expect(text).toContain("anthropic");
    expect(text).toContain("claude-sonnet-4-6");
    expect(text).toContain("parliament");
  });

  it("set rejects invalid tier", async () => {
    const result = await handleConfigTier(mkCtx(), ["set", "ultra", "ollama", "llama3.2"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("low");
  });

  it("set requires provider and model", async () => {
    const result = await handleConfigTier(mkCtx(), ["set", "low", "ollama"]);
    expect(result.kind).toBe("error");
  });

  it("set writes valid tier", async () => {
    const result = await handleConfigTier(mkCtx(), ["set", "high", "anthropic", "claude-opus-4-7"]);
    expect(result.kind).toBe("system-message");
  });

  it("set-default rejects invalid task", async () => {
    const result = await handleConfigTier(mkCtx(), ["set-default", "dancing", "low"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("Task must be one of");
  });

  it("set-default rejects invalid tier", async () => {
    const result = await handleConfigTier(mkCtx(), ["set-default", "conversation", "ultra"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("Tier must be one of");
  });

  it("set-default writes valid task/tier pair", async () => {
    const result = await handleConfigTier(mkCtx(), ["set-default", "parliament", "high"]);
    expect(result.kind).toBe("system-message");
  });

  it("reset requires --confirm", async () => {
    const result = await handleConfigTier(mkCtx(), ["reset"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("--confirm");
  });

  it("reset proceeds with --confirm", async () => {
    const result = await handleConfigTier(mkCtx(), ["reset", "--confirm"]);
    expect(result.kind).toBe("system-message");
  });
});
