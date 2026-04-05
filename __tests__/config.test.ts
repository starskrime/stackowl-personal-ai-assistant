import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { loadConfig, type StackOwlConfig } from "../src/config/loader.js";
import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";

vi.mock("node:fs/promises", () => ({
  readFile: vi.fn(),
  writeFile: vi.fn(),
}));

vi.mock("node:fs", () => ({
  existsSync: vi.fn(),
}));

describe("loadConfig", () => {
  const testDir = "/test/config/dir";

  beforeEach(() => {
    vi.resetAllMocks();
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("when config file does not exist", () => {
    it("should create default config file", async () => {
      vi.mocked(existsSync).mockReturnValue(false);

      const config = await loadConfig(testDir);

      expect(writeFile).toHaveBeenCalledWith(
        join(testDir, "stackowl.config.json"),
        expect.any(String),
        "utf-8",
      );
      expect(config.defaultProvider).toBe("ollama");
      expect(config.defaultModel).toBe("llama3.2");
      expect(config.gateway.port).toBe(3077);
      expect(config.heartbeat.enabled).toBe(false);
      expect(config.owlDna.enabled).toBe(true);
    });

    it("should return a copy of default config", async () => {
      vi.mocked(existsSync).mockReturnValue(false);

      const config = await loadConfig(testDir);

      config.defaultProvider = "modified";
      const config2 = await loadConfig(testDir);
      expect(config2.defaultProvider).toBe("ollama");
    });
  });

  describe("when config file exists", () => {
    const validConfig = {
      defaultProvider: "openai",
      defaultModel: "gpt-4",
      providers: {
        openai: { baseUrl: "https://api.openai.com/v1", apiKey: "sk-test" },
      },
    };

    beforeEach(() => {
      vi.mocked(existsSync).mockReturnValue(true);
    });

    it("should deep merge user config with defaults", async () => {
      vi.mocked(readFile).mockResolvedValue(JSON.stringify(validConfig));

      const config = await loadConfig(testDir);

      expect(config.defaultProvider).toBe("openai");
      expect(config.defaultModel).toBe("gpt-4");
      expect(config.providers.openai).toEqual({
        baseUrl: "https://api.openai.com/v1",
        apiKey: "sk-test",
      });
      expect(config.gateway.port).toBe(3077);
      expect(config.owlDna.enabled).toBe(true);
    });

    it("should merge nested objects correctly", async () => {
      const customGateway = {
        port: 4000,
        host: "0.0.0.0",
        outputMode: "debug" as const,
      };
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          gateway: customGateway,
        }),
      );

      const config = await loadConfig(testDir);

      expect(config.gateway.port).toBe(4000);
      expect(config.gateway.host).toBe("0.0.0.0");
      expect(config.gateway.outputMode).toBe("debug");
      expect(config.gateway.suppressThinkingMessages).toBe(true);
    });

    it("should preserve default providers when not overridden", async () => {
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "custom",
          defaultModel: "custom-model",
          providers: {
            custom: { apiKey: "test-key" },
          },
        }),
      );

      const config = await loadConfig(testDir);

      expect(config.providers.ollama).toBeDefined();
      expect(config.providers.ollama.baseUrl).toBe("http://127.0.0.1:11434");
    });
  });

  describe("validation warnings", () => {
    beforeEach(() => {
      vi.mocked(existsSync).mockReturnValue(true);
    });

    it("should warn when defaultProvider not in providers", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "nonexistent",
          defaultModel: "test",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("nonexistent");
    });

    it("should warn on invalid gateway port (out of range)", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          gateway: { port: 99999 },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("gateway.port");
    });

    it("should not warn on zero gateway port (falsy treated as missing)", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          gateway: { port: 0 },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).not.toHaveBeenCalled();
    });

    it("should warn on parliament.maxRounds out of range", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          parliament: { maxRounds: 15, maxOwls: 6 },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("maxRounds");
    });

    it("should warn on parliament.maxOwls out of range", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          parliament: { maxRounds: 3, maxOwls: 0 },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("maxOwls");
    });

    it("should warn on high maxToolIterations", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          engine: { maxToolIterations: 100 },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("maxToolIterations");
    });

    it("should warn on low maxToolIterations", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          engine: { maxToolIterations: 0 },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("maxToolIterations");
    });

    it("should warn when skills enabled without directories", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          skills: { enabled: true, directories: [] },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("skills");
    });

    it("should warn when smartRouting enabled without availableModels", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          smartRouting: { enabled: true, availableModels: [] },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("smartRouting");
    });

    it("should warn on invalid rateLimit maxPerMinute", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          gateway: { rateLimit: { maxPerMinute: 0, maxPerHour: 100 } },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("maxPerMinute");
    });

    it("should warn on invalid rateLimit maxPerHour", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          gateway: { rateLimit: { maxPerMinute: 10, maxPerHour: 0 } },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("maxPerHour");
    });

    it("should warn on invalid provider baseUrl", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "test",
          defaultModel: "model",
          providers: { test: { baseUrl: "not-a-url" } },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("baseUrl");
    });

    it("should warn on invalid maxContextTokens", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          engine: { maxContextTokens: 500 },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("maxContextTokens");
    });

    it("should warn on invalid owlDna evolutionBatchSize", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          owlDna: { evolutionBatchSize: 0 },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("evolutionBatchSize");
    });

    it("should warn on invalid owlDna decayRatePerWeek", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          owlDna: { decayRatePerWeek: 1.5 },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("decayRatePerWeek");
    });
  });

  describe("error handling", () => {
    beforeEach(() => {
      vi.mocked(existsSync).mockReturnValue(true);
    });

    it("should throw when file read fails", async () => {
      vi.mocked(readFile).mockRejectedValue(new Error("ENOENT: no such file"));

      await expect(loadConfig(testDir)).rejects.toThrow("Failed to load");
    });

    it("should throw when JSON is invalid", async () => {
      vi.mocked(readFile).mockResolvedValue("not valid json {");

      await expect(loadConfig(testDir)).rejects.toThrow("Failed to load");
    });

    it("should throw with config path in error message", async () => {
      vi.mocked(readFile).mockRejectedValue(new Error("permission denied"));

      await expect(loadConfig(testDir)).rejects.toThrow(
        join(testDir, "stackowl.config.json"),
      );
    });

    it("should warn when defaultProvider is not in providers", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "nonexistent",
          defaultModel: "test",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("nonexistent");
    });

    it("should warn when provider baseUrl is invalid", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "test",
          defaultModel: "model",
          providers: { test: { baseUrl: "not-a-url" } },
        }),
      );

      await loadConfig(testDir);

      expect(warnSpy).toHaveBeenCalled();
      expect(warnSpy.mock.calls[0][0]).toContain("baseUrl");
    });
  });

  describe("config merging edge cases", () => {
    beforeEach(() => {
      vi.mocked(existsSync).mockReturnValue(true);
    });

    it("should handle empty user config", async () => {
      vi.mocked(readFile).mockResolvedValue("{}");

      const config = await loadConfig(testDir);

      expect(config.defaultProvider).toBe("ollama");
      expect(config.gateway.port).toBe(3077);
    });

    it("should merge engine config correctly", async () => {
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          engine: { maxToolIterations: 25, maxContextTokens: 12000 },
        }),
      );

      const config = await loadConfig(testDir);

      expect(config.engine?.maxToolIterations).toBe(25);
      expect(config.engine?.maxContextTokens).toBe(12000);
      expect(config.engine?.maxToolResultLength).toBeUndefined();
    });

    it("should merge parliament config correctly", async () => {
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          parliament: { maxRounds: 5 },
        }),
      );

      const config = await loadConfig(testDir);

      expect(config.parliament.maxRounds).toBe(5);
      expect(config.parliament.maxOwls).toBe(6);
    });

    it("should merge heartbeat config correctly", async () => {
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          heartbeat: { enabled: true, intervalMinutes: 60 },
        }),
      );

      const config = await loadConfig(testDir);

      expect(config.heartbeat.enabled).toBe(true);
      expect(config.heartbeat.intervalMinutes).toBe(60);
    });

    it("should merge smartRouting config correctly", async () => {
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          smartRouting: {
            enabled: true,
            availableModels: [{ name: "gpt-4", description: "test" }],
          },
        }),
      );

      const config = await loadConfig(testDir);

      expect(config.smartRouting?.enabled).toBe(true);
      expect(config.smartRouting?.availableModels).toHaveLength(1);
    });

    it("should merge synthesis config correctly", async () => {
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          synthesis: { provider: "openai", model: "gpt-4" },
        }),
      );

      const config = await loadConfig(testDir);

      expect(config.synthesis?.provider).toBe("openai");
      expect(config.synthesis?.model).toBe("gpt-4");
    });

    it("should preserve nested defaults when overriding top-level", async () => {
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          sandboxing: { enabled: false },
        }),
      );

      const config = await loadConfig(testDir);

      expect(config.sandboxing?.enabled).toBe(false);
      expect(config.sandboxing?.debugOutput).toBe(false);
    });

    it("should handle tools config merging", async () => {
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          tools: { maxToolsRouting: 4 },
        }),
      );

      const config = await loadConfig(testDir);

      expect(config.tools?.maxToolsRouting).toBe(4);
      expect(config.tools?.enableIntentRouting).toBe(true);
    });

    it("should handle skills config merging", async () => {
      vi.mocked(readFile).mockResolvedValue(
        JSON.stringify({
          defaultProvider: "ollama",
          defaultModel: "llama3.2",
          providers: { ollama: { baseUrl: "http://127.0.0.1:11434" } },
          skills: { enabled: true, directories: ["./skills"] },
        }),
      );

      const config = await loadConfig(testDir);

      expect(config.skills?.enabled).toBe(true);
      expect(config.skills?.directories).toContain("./skills");
      expect(config.skills?.watch).toBe(false);
    });
  });

  describe("default config structure", () => {
    beforeEach(() => {
      vi.mocked(existsSync).mockReturnValue(false);
    });

    it("should have correct default values for all sections", async () => {
      const config = await loadConfig(testDir);

      expect(config.providers.ollama.baseUrl).toBe("http://127.0.0.1:11434");
      expect(config.providers.ollama.defaultModel).toBe("llama3.2");
      expect(config.providers.ollama.defaultEmbeddingModel).toBe(
        "nomic-embed-text",
      );
      expect(config.workspace).toBe("./workspace");
      expect(config.gateway.host).toBe("127.0.0.1");
      expect(config.parliament.maxRounds).toBe(3);
      expect(config.parliament.maxOwls).toBe(6);
      expect(config.owlDna.evolutionBatchSize).toBe(5);
      expect(config.owlDna.decayRatePerWeek).toBe(0.01);
      expect(config.smartRouting!.enabled).toBe(false);
      expect(config.synthesis!.provider).toBe("anthropic");
      expect(config.synthesis!.model).toBe("claude-sonnet-4-5-20241022");
    });
  });
});
