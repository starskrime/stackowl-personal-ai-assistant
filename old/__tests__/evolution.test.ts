import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { GapDetector } from "../src/evolution/detector.js";
import { CapabilityLedger } from "../src/evolution/ledger.js";
import type { ModelProvider } from "../src/providers/base.js";
import type { ToolProposal } from "../src/evolution/synthesizer.js";

function mockProvider(response: string) {
  return {
    chat: async () => ({ content: response, model: "test", usage: undefined }),
  } as unknown as ModelProvider;
}

function mockProviderThrows(error: Error) {
  return {
    chat: async () => {
      throw error;
    },
  } as unknown as ModelProvider;
}

describe("GapDetector", () => {
  describe("Stage 1: Structured Marker Detection", () => {
    it("should detect gap from structured marker", async () => {
      const detector = new GapDetector();
      const response =
        "I cannot help with that. [CAPABILITY_GAP: needs_file_upload]";
      const provider = mockProvider("NO");

      const result = await detector.detectFromResponse(
        response,
        "upload a file",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.type).toBe("CAPABILITY_GAP");
      expect(result?.description).toBe("needs_file_upload");
      expect(result?.userRequest).toBe("upload a file");
    });

    it("should handle marker with complex content", async () => {
      const detector = new GapDetector();
      const response =
        "Sorry [CAPABILITY_GAP: requires_chrome_automation to control browser]";
      const provider = mockProvider("NO");

      const result = await detector.detectFromResponse(
        response,
        "automate Chrome",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.description).toBe(
        "requires_chrome_automation to control browser",
      );
    });

    it("should be case-insensitive for marker", async () => {
      const detector = new GapDetector();
      const response = "[capability_gap: test_marker]";
      const provider = mockProvider("NO");

      const result = await detector.detectFromResponse(
        response,
        "test",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.description).toBe("test_marker");
    });

    it("should return null when no marker present and no refusal signals", async () => {
      const detector = new GapDetector();
      const response =
        "Here is the information you requested about machine learning.";
      const provider = mockProvider("NO");

      const result = await detector.detectFromResponse(
        response,
        "explain ML",
        provider,
        "test-model",
      );

      expect(result).toBeNull();
    });
  });

  describe("Stage 2: Pre-Filter and LLM Classifier", () => {
    it("should skip classifier when no refusal signals detected", async () => {
      const detector = new GapDetector();
      const response = "I don't have data on that specific topic.";
      const provider = mockProvider("YES");

      const result = await detector.detectFromResponse(
        response,
        "tell me about X",
        provider,
        "test-model",
      );

      expect(result).toBeNull();
    });

    it("should trigger classifier on refusal signal - 'i cannot perform'", async () => {
      const detector = new GapDetector();
      const response = "I cannot perform that action for security reasons.";
      const provider = mockProvider("YES");

      const result = await detector.detectFromResponse(
        response,
        "do the task",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.type).toBe("CAPABILITY_GAP");
    });

    it("should trigger classifier on refusal signal - 'unable to access'", async () => {
      const detector = new GapDetector();
      const response = "I am unable to access your email account.";
      const provider = mockProvider("YES");

      const result = await detector.detectFromResponse(
        response,
        "read my emails",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.type).toBe("CAPABILITY_GAP");
    });

    it("should trigger classifier on refusal signal - 'no tool available'", async () => {
      const detector = new GapDetector();
      const response = "No tool available to take screenshots.";
      const provider = mockProvider("YES");

      const result = await detector.detectFromResponse(
        response,
        "take screenshot",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.type).toBe("CAPABILITY_GAP");
    });

    it("should trigger classifier on refusal signal - 'outside my current capabilities'", async () => {
      const detector = new GapDetector();
      const response = "That is outside my current capabilities.";
      const provider = mockProvider("YES");

      const result = await detector.detectFromResponse(
        response,
        "control the mouse",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.type).toBe("CAPABILITY_GAP");
    });

    it("should trigger classifier on refusal signal - curly quotes", async () => {
      const detector = new GapDetector();
      const response = "I don\u2019t have access to your calendar.";
      const provider = mockProvider("YES");

      const result = await detector.detectFromResponse(
        response,
        "add to calendar",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.type).toBe("CAPABILITY_GAP");
    });

    it("should trigger classifier on refusal signal - straight quotes", async () => {
      const detector = new GapDetector();
      const response = "I don't have a tool to do that.";
      const provider = mockProvider("YES");

      const result = await detector.detectFromResponse(
        response,
        "send a tweet",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.type).toBe("CAPABILITY_GAP");
    });

    it("should return null when classifier answers NO", async () => {
      const detector = new GapDetector();
      const response = "I cannot perform that action for security reasons.";
      const provider = mockProvider("NO");

      const result = await detector.detectFromResponse(
        response,
        "delete all files",
        provider,
        "test-model",
      );

      expect(result).toBeNull();
    });

    it("should return null when classifier throws an error", async () => {
      const detector = new GapDetector();
      const response = "I cannot perform that action.";
      const provider = mockProviderThrows(new Error("LLM down"));

      const result = await detector.detectFromResponse(
        response,
        "do something",
        provider,
        "test-model",
      );

      expect(result).toBeNull();
    });

    it("should handle lowercase YES answer", async () => {
      const detector = new GapDetector();
      const response = "I cannot perform that action.";
      const provider = mockProvider("yes");

      const result = await detector.detectFromResponse(
        response,
        "do something",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.type).toBe("CAPABILITY_GAP");
    });

    it("should handle YES with extra whitespace", async () => {
      const detector = new GapDetector();
      const response = "I cannot perform that action.";
      const provider = mockProvider("  YES  ");

      const result = await detector.detectFromResponse(
        response,
        "do something",
        provider,
        "test-model",
      );

      expect(result).not.toBeNull();
      expect(result?.type).toBe("CAPABILITY_GAP");
    });
  });

  describe("fromMissingTool", () => {
    it("should create TOOL_MISSING gap", () => {
      const detector = new GapDetector();

      const result = detector.fromMissingTool(
        "send_email",
        "send an email to John",
      );

      expect(result.type).toBe("TOOL_MISSING");
      expect(result.attemptedToolName).toBe("send_email");
      expect(result.userRequest).toBe("send an email to John");
      expect(result.description).toContain("send_email");
    });

    it("should handle tool names with underscores and numbers", () => {
      const detector = new GapDetector();

      const result = detector.fromMissingTool(
        "telegram_send_message_v2",
        "send a message",
      );

      expect(result.type).toBe("TOOL_MISSING");
      expect(result.attemptedToolName).toBe("telegram_send_message_v2");
    });
  });
});

describe("CapabilityLedger", () => {
  const TEST_MANIFEST_PATH = "/test/synthesized/_manifest.json";
  let mockReadFile: ReturnType<typeof vi.fn>;
  let mockWriteFile: ReturnType<typeof vi.fn>;
  let mockMkdir: ReturnType<typeof vi.fn>;
  let mockExistsSync: ReturnType<typeof vi.fn>;
  let originalImport: typeof import("node:fs/promises");

  beforeEach(async () => {
    vi.resetModules();

    mockReadFile = vi.fn();
    mockWriteFile = vi.fn();
    mockMkdir = vi.fn();
    mockExistsSync = vi.fn();

    originalImport = await import("node:fs/promises");

    vi.doMock("node:fs/promises", () => ({
      readFile: mockReadFile,
      writeFile: mockWriteFile,
      mkdir: mockMkdir,
    }));

    vi.doMock("node:fs", () => ({
      existsSync: mockExistsSync,
    }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function makeProposal(name = "test_tool"): ToolProposal {
    return {
      toolName: name,
      description: "Test tool description",
      parameters: [],
      rationale: "Testing",
      dependencies: [],
      safetyNote: "none",
      filePath: `/test/path/${name}.ts`,
      owlName: "TestOwl",
      owlEmoji: "🦉",
    };
  }

  describe("load", () => {
    it("should initialize empty manifest when file does not exist", async () => {
      mockExistsSync.mockReturnValue(false);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();

      await ledger.load();

      const all = ledger.listAll();
      expect(all).toHaveLength(0);
    });

    it("should load existing manifest from file", async () => {
      const existingManifest = {
        version: 1,
        tools: [
          {
            toolName: "existing_tool",
            fileName: "existing_tool.ts",
            description: "An existing tool",
            createdAt: "2024-01-01T00:00:00.000Z",
            createdBy: "TestOwl",
            rationale: "Testing",
            dependencies: [],
            safetyNote: "none",
            timesUsed: 5,
            lastUsedAt: "2024-01-02T00:00:00.000Z",
            status: "active",
            consecutiveFailures: 0,
          },
        ],
      };

      mockExistsSync.mockReturnValue(true);
      mockReadFile.mockResolvedValue(JSON.stringify(existingManifest));

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();

      await ledger.load();

      const all = ledger.listAll();
      expect(all).toHaveLength(1);
      expect(all[0].toolName).toBe("existing_tool");
    });

    it("should handle corrupt JSON gracefully", async () => {
      mockExistsSync.mockReturnValue(true);
      mockReadFile.mockResolvedValue("not valid json{");

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();

      await ledger.load();

      const all = ledger.listAll();
      expect(all).toHaveLength(0);
    });
  });

  describe("record", () => {
    it("should add new tool record", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      const proposal = makeProposal("new_tool");
      await ledger.record(proposal);

      const all = ledger.listAll();
      expect(all).toHaveLength(1);
      expect(all[0].toolName).toBe("new_tool");
      expect(all[0].createdBy).toBe("TestOwl");
      expect(all[0].status).toBe("active");
      expect(all[0].timesUsed).toBe(0);
    });

    it("should update existing tool record", async () => {
      const existingManifest = {
        version: 1,
        tools: [
          {
            toolName: "update_tool",
            fileName: "update_tool.ts",
            description: "Old description",
            createdAt: "2024-01-01T00:00:00.000Z",
            createdBy: "OldOwl",
            rationale: "Old rationale",
            dependencies: ["old-dep"],
            safetyNote: "old safety",
            timesUsed: 10,
            status: "active",
            consecutiveFailures: 0,
          },
        ],
      };

      mockExistsSync.mockReturnValue(true);
      mockReadFile.mockResolvedValue(JSON.stringify(existingManifest));
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      const proposal = makeProposal("update_tool");
      proposal.description = "New description";
      await ledger.record(proposal);

      const all = ledger.listAll();
      expect(all).toHaveLength(1);
      expect(all[0].description).toBe("New description");
      expect(all[0].createdBy).toBe("TestOwl");
    });
  });

  describe("recordUsage", () => {
    it("should increment usage count on success", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      const proposal = makeProposal("usage_tool");
      await ledger.record(proposal);

      await ledger.recordUsage("usage_tool", true);
      await ledger.recordUsage("usage_tool", true);

      const all = ledger.listAll();
      expect(all[0].timesUsed).toBe(2);
      expect(all[0].consecutiveFailures).toBe(0);
    });

    it("should increment failures on unsuccessful use", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      const proposal = makeProposal("failing_tool");
      await ledger.record(proposal);

      await ledger.recordUsage("failing_tool", false);
      await ledger.recordUsage("failing_tool", false);

      const all = ledger.listAll();
      expect(all[0].consecutiveFailures).toBe(2);
      expect(all[0].status).toBe("active");
    });

    it("should mark tool as failed after 3 consecutive failures", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      const proposal = makeProposal("flaky_tool");
      await ledger.record(proposal);

      await ledger.recordUsage("flaky_tool", false);
      await ledger.recordUsage("flaky_tool", false);
      await ledger.recordUsage("flaky_tool", false);

      const all = ledger.listAll();
      expect(all[0].status).toBe("failed");
      expect(all[0].consecutiveFailures).toBe(3);
    });

    it("should restore failed tool to active on success", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      const proposal = makeProposal("recovering_tool");
      await ledger.record(proposal);

      await ledger.recordUsage("recovering_tool", false);
      await ledger.recordUsage("recovering_tool", false);
      await ledger.recordUsage("recovering_tool", false);
      await ledger.recordUsage("recovering_tool", true);

      const all = ledger.listAll();
      expect(all[0].status).toBe("active");
      expect(all[0].consecutiveFailures).toBe(0);
    });

    it("should do nothing for unknown tool", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      await ledger.recordUsage("nonexistent_tool", true);

      expect(mockWriteFile).not.toHaveBeenCalled();
    });
  });

  describe("retire", () => {
    it("should mark tool as retired", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      const proposal = makeProposal("retire_me");
      await ledger.record(proposal);
      await ledger.retire("retire_me");

      const all = ledger.listAll();
      expect(all[0].status).toBe("retired");
    });

    it("should return false for unknown tool", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      const result = await ledger.retire("nonexistent");

      expect(result).toBe(false);
    });
  });

  describe("listActive", () => {
    it("should return only active tools", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      await ledger.record(makeProposal("active_tool"));
      await ledger.record(makeProposal("retireable_tool"));
      await ledger.retire("retireable_tool");

      const active = ledger.listActive();

      expect(active).toHaveLength(1);
      expect(active[0].toolName).toBe("active_tool");
    });
  });

  describe("listAll", () => {
    it("should return all tools regardless of status", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      await ledger.record(makeProposal("tool1"));
      await ledger.record(makeProposal("tool2"));
      await ledger.retire("tool1");

      const all = ledger.listAll();

      expect(all).toHaveLength(2);
    });
  });

  describe("findExisting", () => {
    beforeEach(() => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);
    });

    it("should return undefined when no active tools", async () => {
      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      const result = await ledger.findExisting("take a screenshot");

      expect(result).toBeUndefined();
    });

    it("should return undefined for request with no 5+ char words", async () => {
      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      await ledger.record(makeProposal("screenshot_tool"));

      const result = await ledger.findExisting("do it");

      expect(result).toBeUndefined();
    });

    it("should find tool via Tier-1 name match", async () => {
      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      await ledger.record(makeProposal("screenshot_capture"));

      const result = await ledger.findExisting(
        "take a screenshot of my screen",
      );

      expect(result).not.toBeUndefined();
      expect(result?.toolName).toBe("screenshot_capture");
    });

    it("should find tool via Tier-2 keyword scoring", async () => {
      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      await ledger.record({
        ...makeProposal("screen_recorder"),
        description: "Records the screen and saves video",
      });

      const result = await ledger.findExisting(
        "I need to capture my desktop to a video file",
      );

      expect(result).not.toBeUndefined();
      expect(result?.toolName).toBe("screen_recorder");
    });

    it("should return best match when multiple tools score", async () => {
      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      await ledger.record({
        ...makeProposal("email_sender"),
        description: "Sends email messages",
      });
      await ledger.record({
        ...makeProposal("calendar_viewer"),
        description: "Views calendar events",
      });

      const result = await ledger.findExisting("send an email to my team");

      expect(result).not.toBeUndefined();
      expect(result?.toolName).toBe("email_sender");
    });

    it("should not match tools below 0.25 score threshold", async () => {
      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      await ledger.record({
        ...makeProposal("unrelated_tool"),
        description: "Does something completely different",
      });

      const result = await ledger.findExisting("send a quick email message");

      expect(result).toBeUndefined();
    });
  });

  describe("getStats", () => {
    it("should return stats for all tools", async () => {
      mockExistsSync.mockReturnValue(false);
      mockWriteFile.mockResolvedValue(undefined);
      mockMkdir.mockResolvedValue(undefined);

      const { CapabilityLedger } = await import("../src/evolution/ledger.js");
      const ledger = new CapabilityLedger();
      await ledger.load();

      await ledger.record(makeProposal("synthesized_tool"));
      await ledger.record({
        ...makeProposal("system_tool"),
        owlName: "system",
      });

      await ledger.recordUsage("synthesized_tool", true);
      await ledger.recordUsage("synthesized_tool", false);

      const stats = await ledger.getStats();

      expect(stats["synthesized_tool"]).toEqual({
        isSynthesized: true,
        consecutiveFailures: 1,
        totalUses: 2,
        lastUsedAt: expect.any(String),
      });

      expect(stats["system_tool"]).toEqual({
        isSynthesized: false,
        consecutiveFailures: 0,
        totalUses: 0,
        lastUsedAt: undefined,
      });
    });
  });
});
