import { describe, it, expect, vi, beforeEach } from "vitest";
import { handleConfigEngine } from "../../../../../src/cli/v2/commands/handlers/config/engine.js";
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

describe("/config engine", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows error for unknown verb", async () => {
    const result = await handleConfigEngine(mkCtx(), ["bogus"]);
    expect(result.kind).toBe("error");
  });

  it("list shows all engine keys with defaults", async () => {
    const result = await handleConfigEngine(mkCtx(), ["list"]);
    expect(result.kind).toBe("system-message");
    const text = (result as { kind: "system-message"; text: string }).text;
    expect(text).toContain("maxToolIterations");
    expect(text).toContain("maxRetries");
    expect(text).toContain("dnaBaseTemp");
  });

  it("list shows current values when set", async () => {
    const cfg = mkConfig({ engine: { maxRetries: 5 } });
    const result = await handleConfigEngine(mkCtx(cfg), ["list"]);
    const text = (result as { kind: "system-message"; text: string }).text;
    expect(text).toContain("5");
  });

  it("set requires key and value", async () => {
    const result = await handleConfigEngine(mkCtx(), ["set", "maxRetries"]);
    expect(result.kind).toBe("error");
  });

  it("set rejects unknown key", async () => {
    const result = await handleConfigEngine(mkCtx(), ["set", "unknownKey", "5"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("Unknown engine key");
  });

  it("set rejects non-numeric value", async () => {
    const result = await handleConfigEngine(mkCtx(), ["set", "maxRetries", "abc"]);
    expect(result.kind).toBe("error");
  });

  it("set accepts integer value", async () => {
    const result = await handleConfigEngine(mkCtx(), ["set", "maxRetries", "5"]);
    expect(result.kind).toBe("system-message");
  });

  it("set accepts float for float keys", async () => {
    const result = await handleConfigEngine(mkCtx(), ["set", "dnaBaseTemp", "0.9"]);
    expect(result.kind).toBe("system-message");
  });

  it("reset requires --confirm", async () => {
    const result = await handleConfigEngine(mkCtx(), ["reset"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("--confirm");
  });

  it("reset proceeds with --confirm", async () => {
    const result = await handleConfigEngine(mkCtx(), ["reset", "--confirm"]);
    expect(result.kind).toBe("system-message");
  });
});
