import { describe, it, expect, beforeEach, vi } from "vitest";
import { PreferenceRecognizer } from "../../src/memory/preference-recognizer.js";
import type { FactStore } from "../../src/memory/fact-store.js";

function makeMockFactStore(): FactStore {
  return {
    add: vi.fn().mockResolvedValue({
      id: "fact_1",
      userId: "default",
      fact: "Test fact",
      category: "preference",
      confidence: 0.8,
      source: "explicit",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      accessCount: 0,
    }),
  } as unknown as FactStore;
}

describe("PreferenceRecognizer", () => {
  let recognizer: PreferenceRecognizer;

  beforeEach(() => {
    recognizer = new PreferenceRecognizer();
  });

  describe("recognizeFromMessage()", () => {
    it("extracts explicit preferences", async () => {
      const prefs = await recognizer.recognizeFromMessage("I prefer concise responses");

      const concise = prefs.find((p) => p.key === "preferred_response_length");
      expect(concise).toBeDefined();
      expect(concise!.value).toBe("concise");
      expect(concise!.source).toBe("explicit");
    });

    it("extracts name from message", async () => {
      const prefs = await recognizer.recognizeFromMessage("Call me John");

      const name = prefs.find((p) => p.key === "name");
      expect(name).toBeDefined();
      expect(name!.value).toBe("John");
      expect(name!.source).toBe("explicit");
    });

    it("extracts 'my name is' pattern", async () => {
      const prefs = await recognizer.recognizeFromMessage("My name is Jane");

      const name = prefs.find((p) => p.key === "name");
      expect(name).toBeDefined();
      expect(name!.value).toBe("Jane");
    });

    it("extracts tool preferences", async () => {
      const prefs = await recognizer.recognizeFromMessage("Use VS Code for editing");

      const toolPref = prefs.find((p) => p.key === "tool_preference");
      expect(toolPref).toBeDefined();
      expect(toolPref!.value).toBe("VS Code for editing");
    });

    it("extracts tool avoidance", async () => {
      const prefs = await recognizer.recognizeFromMessage("Don't use Vim");

      const avoid = prefs.find((p) => p.key === "tool_avoidance");
      expect(avoid).toBeDefined();
      expect(avoid!.value).toBe("Vim");
    });

    it("extracts implicit language preference (Chinese)", async () => {
      const prefs = await recognizer.recognizeFromMessage("你好，今天天气怎么样？");

      const lang = prefs.find((p) => p.key === "language");
      expect(lang).toBeDefined();
      expect(lang!.value).toBe("zh");
      expect(lang!.source).toBe("inferred");
    });

    it("extracts implicit language preference (Russian)", async () => {
      const prefs = await recognizer.recognizeFromMessage("Привет, как дела?");

      const lang = prefs.find((p) => p.key === "language");
      expect(lang).toBeDefined();
      expect(lang!.value).toBe("ru");
    });

    it("extracts emoji usage", async () => {
      const prefs = await recognizer.recognizeFromMessage("Hello! How are you? 😊");

      const emoji = prefs.find((p) => p.key === "uses_emoji");
      expect(emoji).toBeDefined();
      expect(emoji!.value).toBe(true);
    });

    it("returns empty array for regular message", async () => {
      const prefs = await recognizer.recognizeFromMessage("What is the weather today?");

      expect(prefs.length).toBe(0);
    });

    it("extracts multiple preferences from single message", async () => {
      const prefs = await recognizer.recognizeFromMessage("I like coffee, call me Dave");

      const likes = prefs.find((p) => p.key === "likes");
      const name = prefs.find((p) => p.key === "name");

      expect(likes).toBeDefined();
      expect(name).toBeDefined();
    });

    it("records signals after recognition", async () => {
      await recognizer.recognizeFromMessage("Call me Bob");

      const signals = recognizer.getSignals();
      expect(signals.length).toBeGreaterThan(0);
    });
  });

  describe("getSignals()", () => {
    it("returns all signals", async () => {
      await recognizer.recognizeFromMessage("Call me Alice");
      await recognizer.recognizeFromMessage("I prefer detailed responses");

      const signals = recognizer.getSignals();
      expect(signals.length).toBeGreaterThanOrEqual(2);
    });

    it("returns signals with correct metadata", async () => {
      await recognizer.recognizeFromMessage("Hello");

      const signals = recognizer.getSignals();
      const first = signals[0];
      expect(first.type).toBeDefined();
      expect(first.value).toBeDefined();
      expect(first.timestamp).toBeGreaterThan(0);
    });
  });

  describe("getSignalsByType()", () => {
    it("filters signals by type", async () => {
      await recognizer.recognizeFromMessage("I like coffee");
      await recognizer.recognizeFromMessage("I like tea");

      const likeSignals = recognizer.getSignalsByType("likes");
      expect(likeSignals.length).toBeGreaterThanOrEqual(2);
    });

    it("returns empty array for unknown type", () => {
      const signals = recognizer.getSignalsByType("unknown_type");
      expect(signals).toHaveLength(0);
    });
  });

  describe("buildContextString()", () => {
    it("returns empty string when no preferences", () => {
      const context = recognizer.buildContextString();
      expect(context).toBe("");
    });

    it("includes high-confidence preferences", async () => {
      await recognizer.recognizeFromMessage("Call me John");
      await recognizer.recognizeFromMessage("My name is Jane");

      const context = recognizer.buildContextString();
      expect(context).toContain("Recognized Preferences");
    });

    it("respects minimum confidence threshold", async () => {
      await recognizer.recognizeFromMessage("Hello");

      const context = recognizer.buildContextString(0.9);
      expect(context).toBe("");
    });

    it("includes source information", async () => {
      await recognizer.recognizeFromMessage("Call me Mike");

      const context = recognizer.buildContextString();
      expect(context).toContain("explicit");
    });
  });

  describe("getPreferenceSummary()", () => {
    it("returns correct summary counts", async () => {
      await recognizer.recognizeFromMessage("Call me John");
      await recognizer.recognizeFromMessage("I prefer concise");
      await recognizer.recognizeFromMessage("Hello");

      const summary = recognizer.getPreferenceSummary();
      expect(summary.high + summary.medium + summary.low).toBeGreaterThan(0);
    });

    it("categorizes preferences", async () => {
      await recognizer.recognizeFromMessage("Call me John");
      await recognizer.recognizeFromMessage("Use VS Code");

      const summary = recognizer.getPreferenceSummary();
      expect(Object.keys(summary.categories).length).toBeGreaterThan(0);
    });
  });

  describe("with FactStore integration", () => {
    it("persists high-confidence preferences", async () => {
      const factStore = makeMockFactStore();
      const recognizerWithStore = new PreferenceRecognizer(factStore);

      await recognizerWithStore.recognizeFromMessage("Call me important person");

      expect(factStore.add).toHaveBeenCalled();
    });

    it("does not persist low-confidence preferences", async () => {
      const factStore = makeMockFactStore();
      const recognizerWithStore = new PreferenceRecognizer(factStore);

      // Use a message with no preference patterns
      await recognizerWithStore.recognizeFromMessage("What is the capital of France?");

      expect(factStore.add).not.toHaveBeenCalled();
    });
  });

  describe("edge cases", () => {
    it("handles empty message", async () => {
      const prefs = await recognizer.recognizeFromMessage("");
      expect(prefs).toHaveLength(0);
    });

    it("handles very long message", async () => {
      const longMessage = "A".repeat(1000);
      const prefs = await recognizer.recognizeFromMessage(longMessage);
      expect(prefs.length).toBeGreaterThanOrEqual(0);
    });

    it("handles unicode characters", async () => {
      const prefs = await recognizer.recognizeFromMessage("你好！我喜欢咖啡☕");
      expect(prefs.length).toBeGreaterThan(0);
    });
  });
});
