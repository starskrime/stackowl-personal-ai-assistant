import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import * as loaderModule from "../../src/config/loader.js";
import * as patchModule from "../../src/config/patch.js";
import { configReloadBus } from "../../src/config/reload-bus.js";
import type { StackOwlConfig } from "../../src/config/loader.js";

function mkConfig(): StackOwlConfig {
  return {
    providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
    defaultProvider: "ollama",
    defaultModel: "llama3.2",
    workspace: "./workspace",
    gateway: { port: 3077, host: "127.0.0.1" },
    parliament: { maxRounds: 3, maxOwls: 6 },
    heartbeat: { enabled: false, intervalMinutes: 30 },
    owlDna: { enabled: true, evolutionBatchSize: 5, decayRatePerWeek: 0.1 },
  };
}

describe("patchConfig", () => {
  let saveSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    configReloadBus.reset();
    // spy on saveConfig in the loader module — patch.ts imports it from there
    saveSpy = vi.spyOn(loaderModule, "saveConfig").mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("deep-merges patch without clobbering sibling fields", async () => {
    const cfg = mkConfig();
    cfg.engine = { maxToolIterations: 10, maxContextTokens: 5000 };
    await patchModule.patchConfig(cfg, "engine", { maxToolIterations: 20 }, "/tmp/test");
    expect(cfg.engine?.maxToolIterations).toBe(20);
    expect(cfg.engine?.maxContextTokens).toBe(5000);
  });

  it("calls saveConfig with the mutated config", async () => {
    const cfg = mkConfig();
    await patchModule.patchConfig(cfg, "heartbeat", { intervalMinutes: 60 }, "/tmp/test");
    expect(saveSpy).toHaveBeenCalledWith("/tmp/test", cfg);
  });

  it("rolls back in-memory state when saveConfig fails", async () => {
    const cfg = mkConfig();
    cfg.engine = { maxToolIterations: 10 };
    saveSpy.mockRejectedValueOnce(new Error("disk full"));
    await expect(
      patchModule.patchConfig(cfg, "engine", { maxToolIterations: 99 }, "/tmp/test"),
    ).rejects.toThrow("disk full");
    expect(cfg.engine?.maxToolIterations).toBe(10);
  });

  it("emits on configReloadBus for hot-reloadable sections", async () => {
    const cfg = mkConfig();
    const handler = vi.fn().mockResolvedValue(undefined);
    configReloadBus.on("heartbeat", handler);
    await patchModule.patchConfig(cfg, "heartbeat", { intervalMinutes: 60 }, "/tmp/test");
    expect(handler).toHaveBeenCalledWith(
      expect.objectContaining({ intervalMinutes: 60 }),
      expect.objectContaining({ intervalMinutes: 30 }),
    );
  });

  it("does NOT emit on bus when restartRequired is true", async () => {
    const cfg = mkConfig();
    const handler = vi.fn().mockResolvedValue(undefined);
    configReloadBus.on("telegram", handler as never);
    await patchModule.patchConfig(
      cfg,
      "telegram",
      { botToken: "tok" },
      "/tmp/test",
      { restartRequired: true },
    );
    expect(handler).not.toHaveBeenCalled();
  });

  it("returns { hotReloaded: true, restartRequired: false } for hot-reloadable section", async () => {
    const cfg = mkConfig();
    const result = await patchModule.patchConfig(cfg, "engine", { maxRetries: 5 }, "/tmp/test");
    expect(result).toEqual({ hotReloaded: true, restartRequired: false });
  });

  it("returns { hotReloaded: false, restartRequired: true } when caller sets restartRequired", async () => {
    const cfg = mkConfig();
    const result = await patchModule.patchConfig(
      cfg,
      "telegram",
      { botToken: "tok" },
      "/tmp/test",
      { restartRequired: true },
    );
    expect(result).toEqual({ hotReloaded: false, restartRequired: true });
  });

  it("rolls back disk write on bus handler failure", async () => {
    const cfg = mkConfig();
    cfg.heartbeat.intervalMinutes = 30;
    configReloadBus.on("heartbeat", async () => {
      throw new Error("handler failed");
    });
    await expect(
      patchModule.patchConfig(cfg, "heartbeat", { intervalMinutes: 99 }, "/tmp/test"),
    ).rejects.toThrow("handler failed");
    expect(cfg.heartbeat.intervalMinutes).toBe(30);
    expect(saveSpy).toHaveBeenCalledTimes(2);
  });

  it("handles patching an undefined section (sets it directly)", async () => {
    const cfg = mkConfig();
    await patchModule.patchConfig(cfg, "engine", { maxRetries: 5 }, "/tmp/test");
    expect(cfg.engine?.maxRetries).toBe(5);
  });
});

describe("configReloadBus", () => {
  beforeEach(() => configReloadBus.reset());

  it("calls all registered handlers for a section", async () => {
    const h1 = vi.fn().mockResolvedValue(undefined);
    const h2 = vi.fn().mockResolvedValue(undefined);
    configReloadBus.on("logging", h1);
    configReloadBus.on("logging", h2);
    const next = { level: "debug" as const };
    const prev = { level: "info" as const };
    await configReloadBus.emit("logging", next, prev);
    expect(h1).toHaveBeenCalledWith(next, prev);
    expect(h2).toHaveBeenCalledWith(next, prev);
  });

  it("does not call handlers for other sections", async () => {
    const handler = vi.fn().mockResolvedValue(undefined);
    configReloadBus.on("heartbeat", handler);
    await configReloadBus.emit("logging", { level: "debug" as const }, { level: "info" as const });
    expect(handler).not.toHaveBeenCalled();
  });

  it("reset clears all handlers", async () => {
    const handler = vi.fn().mockResolvedValue(undefined);
    configReloadBus.on("heartbeat", handler);
    configReloadBus.reset();
    await configReloadBus.emit("heartbeat", { enabled: true, intervalMinutes: 30 }, { enabled: false, intervalMinutes: 30 });
    expect(handler).not.toHaveBeenCalled();
  });
});
