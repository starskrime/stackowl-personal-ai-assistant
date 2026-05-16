import { describe, it, expect, vi, beforeEach } from "vitest";
import { handleConfigChannel } from "../../../../../src/cli/v2/commands/handlers/config/channel.js";
import type { CommandContext } from "../../../../../src/cli/v2/commands/registry.js";
import type { StackOwlConfig } from "../../../../../src/config/loader.js";

vi.mock("../../../../../src/config/patch.js", () => ({
  patchConfig: vi.fn().mockResolvedValue({ hotReloaded: false, restartRequired: true }),
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

describe("/config channel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows error for unknown verb", async () => {
    const result = await handleConfigChannel(mkCtx(), ["bogus"]);
    expect(result.kind).toBe("error");
  });

  it("list shows not-configured for unconfigured channels", async () => {
    const result = await handleConfigChannel(mkCtx(), ["list"]);
    expect(result.kind).toBe("system-message");
    const text = (result as { kind: "system-message"; text: string }).text;
    expect(text).toContain("not configured");
  });

  it("list masks telegram token", async () => {
    const cfg = mkConfig({ telegram: { botToken: "1234567890:ABCDEFGHIJKLMNOP" } });
    const result = await handleConfigChannel(mkCtx(cfg), ["list"]);
    const text = (result as { kind: "system-message"; text: string }).text;
    expect(text).not.toContain("1234567890:ABCDEFGHIJKLMNOP");
    expect(text).toContain("…");
  });

  // telegram
  it("telegram set-token requires a token", async () => {
    const result = await handleConfigChannel(mkCtx(), ["telegram", "set-token"]);
    expect(result.kind).toBe("error");
  });

  it("telegram set-token saves successfully", async () => {
    const cfg = mkConfig({ telegram: { botToken: "old:token" } });
    const result = await handleConfigChannel(mkCtx(cfg), ["telegram", "set-token", "new:token"]);
    expect(result.kind).toBe("system-message");
  });

  it("telegram add-user rejects non-numeric id", async () => {
    const cfg = mkConfig({ telegram: { botToken: "tok" } });
    const result = await handleConfigChannel(mkCtx(cfg), ["telegram", "add-user", "abc"]);
    expect(result.kind).toBe("error");
  });

  it("telegram add-user rejects duplicate", async () => {
    const cfg = mkConfig({ telegram: { botToken: "tok", allowedUserIds: [123] } });
    const result = await handleConfigChannel(mkCtx(cfg), ["telegram", "add-user", "123"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("already");
  });

  it("telegram remove-user rejects absent id", async () => {
    const cfg = mkConfig({ telegram: { botToken: "tok", allowedUserIds: [123] } });
    const result = await handleConfigChannel(mkCtx(cfg), ["telegram", "remove-user", "999"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("not in");
  });

  it("telegram shows error for unknown subverb", async () => {
    const result = await handleConfigChannel(mkCtx(), ["telegram", "bogus"]);
    expect(result.kind).toBe("error");
  });

  // discord
  it("discord set-dm-policy rejects invalid policy", async () => {
    const result = await handleConfigChannel(mkCtx(), ["discord", "set-dm-policy", "everyone"]);
    expect(result.kind).toBe("error");
    expect((result as { kind: "error"; text: string }).text).toContain("open");
  });

  it("discord set-dm-policy accepts valid policy", async () => {
    const cfg = mkConfig({ discord: { botToken: "tok" } });
    const result = await handleConfigChannel(mkCtx(cfg), ["discord", "set-dm-policy", "open"]);
    expect(result.kind).toBe("system-message");
  });

  // whatsapp
  it("whatsapp enable returns system-message", async () => {
    const result = await handleConfigChannel(mkCtx(), ["whatsapp", "enable"]);
    expect(result.kind).toBe("system-message");
  });

  it("whatsapp set-dm-policy rejects invalid policy", async () => {
    const result = await handleConfigChannel(mkCtx(), ["whatsapp", "set-dm-policy", "everyone"]);
    expect(result.kind).toBe("error");
  });
});
