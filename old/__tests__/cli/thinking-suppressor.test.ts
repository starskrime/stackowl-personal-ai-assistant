import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { ThinkingSuppressor, createThinkingSuppressor } from "../../src/cli/thinking-suppressor.js";
import type { StreamEvent } from "../../src/providers/base.js";

function makeTextDelta(content: string): StreamEvent {
  return { type: "text_delta", content } as StreamEvent;
}
function makeToolStart(toolCallId: string, toolName: string): StreamEvent {
  return { type: "tool_start", toolCallId, toolName } as StreamEvent;
}
function makeToolEnd(toolCallId: string, toolName: string): StreamEvent {
  return { type: "tool_end", toolCallId, toolName, arguments: "{}" } as StreamEvent;
}
function makeDone(): StreamEvent {
  return { type: "done", usage: { promptTokens: 0, completionTokens: 0, totalTokens: 0 } } as StreamEvent;
}

describe("ThinkingSuppressor", () => {
  afterEach(() => {
    delete process.env.STACKOWL_SUPPRESS_THINKING;
    delete process.env.STACKOWL_JSON;
  });

  describe("isActive", () => {
    it("defaults to inactive without flags", () => {
      const suppressor = new ThinkingSuppressor();
      expect(suppressor.isActive()).toBe(false);
    });

    it("activates when STACKOWL_SUPPRESS_THINKING is true", () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();
      expect(suppressor.isActive()).toBe(true);
    });
  });

  describe("enable/disable", () => {
    it("enables full suppression", () => {
      const suppressor = new ThinkingSuppressor();
      suppressor.enable("full");
      expect(suppressor.isActive()).toBe(true);
      expect(suppressor.getLevel()).toBe("full");
    });

    it("disables suppression", () => {
      const suppressor = new ThinkingSuppressor();
      suppressor.enable("full");
      suppressor.disable();
      expect(suppressor.isActive()).toBe(false);
    });
  });

  describe("processEvent", () => {
    it("passes through all events when inactive", () => {
      const suppressor = new ThinkingSuppressor();
      expect(suppressor.processEvent(makeTextDelta("hello"))).toBe(false);
      expect(suppressor.processEvent(makeToolStart("1", "shell"))).toBe(false);
    });

    it("suppresses text deltas when active", () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();

      expect(suppressor.processEvent(makeTextDelta("thinking..."))).toBe(true);
      expect(suppressor.getMessageCount()).toBe(1);
    });

    it("suppresses tool_start when active", () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();

      expect(suppressor.processEvent(makeToolStart("1", "shell"))).toBe(true);
    });

    it("suppresses tool_end when active", () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();

      expect(suppressor.processEvent(makeToolEnd("1", "shell"))).toBe(true);
    });

    it("lets done events through", () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();

      expect(suppressor.processEvent(makeDone())).toBe(false);
    });
  });

  describe("buffered deltas", () => {
    it("collects deltas in buffer when suppressed", () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();

      suppressor.processEvent(makeTextDelta("hello "));
      suppressor.processEvent(makeTextDelta("world"));

      expect(suppressor.getBufferedContent()).toBe("hello world");
    });

    it("clears buffer", () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();

      suppressor.processEvent(makeTextDelta("test"));
      suppressor.clearBuffer();

      expect(suppressor.getBufferedContent()).toBe("");
    });
  });

  describe("shouldShowToolCalls", () => {
    it("returns true when inactive", () => {
      const suppressor = new ThinkingSuppressor();
      expect(suppressor.shouldShowToolCalls()).toBe(true);
    });

    it("returns false when active", () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();
      expect(suppressor.shouldShowToolCalls()).toBe(false);
    });
  });

  describe("shouldShowThinking", () => {
    it("returns true when inactive", () => {
      const suppressor = new ThinkingSuppressor();
      expect(suppressor.shouldShowThinking()).toBe(true);
    });

    it("returns false when active", () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();
      expect(suppressor.shouldShowThinking()).toBe(false);
    });
  });

  describe("createSuppressedCallback", () => {
    it("wraps original callback and applies suppression", async () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();

      const original = vi.fn();
      const wrapped = suppressor.createSuppressedCallback(original);

      await wrapped(makeTextDelta("test"));
      expect(original).not.toHaveBeenCalled();

      await wrapped(makeDone());
      expect(original).toHaveBeenCalled();
    });
  });

  describe("createProgressCallback", () => {
    it("suppresses progress when active", async () => {
      process.env.STACKOWL_SUPPRESS_THINKING = "true";
      const suppressor = new ThinkingSuppressor();

      const original = vi.fn();
      const wrapped = suppressor.createProgressCallback(original);

      await wrapped("_Thinking..._");
      expect(original).not.toHaveBeenCalled();
    });

    it("passes through when inactive", async () => {
      const suppressor = new ThinkingSuppressor();

      const original = vi.fn();
      const wrapped = suppressor.createProgressCallback(original);

      await wrapped("_Thinking..._");
      expect(original).toHaveBeenCalledWith("_Thinking..._");
    });
  });
});

describe("createThinkingSuppressor", () => {
  it("creates instance with options", () => {
    const suppressor = createThinkingSuppressor({ defaultLevel: "full" });
    expect(suppressor.isActive()).toBe(true);
  });

  it("respects quiet arg in argv", () => {
    process.argv.push("--quiet");
    const suppressor = createThinkingSuppressor();
    expect(suppressor.isActive()).toBe(true);
    process.argv.pop();
  });

  it("activates in json mode", () => {
    process.env.STACKOWL_JSON = "true";
    const suppressor = createThinkingSuppressor();
    expect(suppressor.isActive()).toBe(true);
    delete process.env.STACKOWL_JSON;
  });
});