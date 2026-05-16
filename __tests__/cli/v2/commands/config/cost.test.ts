import { describe, it, expect, vi, beforeEach } from "vitest";
import { handleConfigCost } from "../../../../../src/cli/v2/commands/handlers/config/cost.js";
import type { CommandContext } from "../../../../../src/cli/v2/commands/registry.js";
import type { StackOwlConfig } from "../../../../../src/config/loader.js";

vi.mock("../../../../../src/config/patch.js", () => ({
  patchConfig: vi.fn().mockResolvedValue({ hotReloaded: true, restartRequired: false }),
}));

function mkConfig(overrides: Partial<StackOwlConfig> = {}): StackOwlConfig {
  return {
    providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
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

describe("/config cost", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows error for unknown verb", async () => {
    const result = await handleConfigCost(mkCtx(), ["bogus"]);
    expect(result.kind).toBe("error");
  });

  it("list shows disabled when not configured", async () => {
    const result = await handleConfigCost(mkCtx(), ["list"]);
    expect(result.kind).toBe("system-message");
    expect((result as { kind: "system-message"; text: string }).text).toContain("disabled");
  });

  it("list shows budget values when configured", async () => {
    const cfg = mkConfig({ costs: { enabled: true, budget: { maxDailyUsd: 5, warnAtPercent: 80 } } });
    const result = await handleConfigCost(mkCtx(cfg), ["list"]);
    const text = (result as { kind: "system-message"; text: string }).text;
    expect(text).toContain("enabled");
    expect(text).toContain("5");
    expect(text).toContain("80");
  });

  it("enable returns system-message", async () => {
    const result = await handleConfigCost(mkCtx(), ["enable"]);
    expect(result.kind).toBe("system-message");
  });

  it("disable returns system-message", async () => {
    const result = await handleConfigCost(mkCtx(), ["disable"]);
    expect(result.kind).toBe("system-message");
  });

  it("set-budget requires key and value", async () => {
    const result = await handleConfigCost(mkCtx(), ["set-budget", "maxDailyUsd"]);
    expect(result.kind).toBe("error");
  });

  it("set-budget rejects unknown key", async () => {
    const result = await handleConfigCost(mkCtx(), ["set-budget", "unknownKey", "5"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("Unknown budget key");
  });

  it("set-budget rejects negative value", async () => {
    const result = await handleConfigCost(mkCtx(), ["set-budget", "maxDailyUsd", "-1"]);
    expect(result.kind).toBe("error");
  });

  it("set-budget sets valid value", async () => {
    const result = await handleConfigCost(mkCtx(), ["set-budget", "maxDailyUsd", "10"]);
    expect(result.kind).toBe("system-message");
  });

  it("reset requires --confirm", async () => {
    const result = await handleConfigCost(mkCtx(), ["reset"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("--confirm");
  });

  it("reset proceeds with --confirm", async () => {
    const result = await handleConfigCost(mkCtx(), ["reset", "--confirm"]);
    expect(result.kind).toBe("system-message");
  });
});
