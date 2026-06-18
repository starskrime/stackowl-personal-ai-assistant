import { describe, it, expect, beforeEach, vi } from "vitest";
import { ToolStream, createToolStream } from "../../src/cli/tool-stream.js";
import type { StreamEvent } from "../../src/providers/base.js";

function makeToolStart(toolCallId: string, toolName: string): StreamEvent {
  return { type: "tool_start", toolCallId, toolName } as StreamEvent;
}
function makeToolArgsDelta(toolCallId: string, argsDelta: string): StreamEvent {
  return { type: "tool_args_delta", toolCallId, argsDelta } as StreamEvent;
}
function makeToolEnd(toolCallId: string, toolName: string): StreamEvent {
  return { type: "tool_end", toolCallId, toolName, arguments: "{}" } as StreamEvent;
}
function makeTextDelta(content: string): StreamEvent {
  return { type: "text_delta", content } as StreamEvent;
}

describe("ToolStream", () => {
  describe("createStreamHandler", () => {
    it("tracks tool_start events", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));

      const running = stream.getRunningTools();
      expect(running).toHaveLength(1);
      expect(running[0]!.toolName).toBe("shell");
      expect(running[0]!.status).toBe("running");
    });

    it("tracks tool_args_delta events", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      handler(makeToolArgsDelta("call-1", '{"command":"ls'));

      const tool = stream.getTool("call-1");
      expect(tool!.arguments).toBe('{"command":"ls');
    });

    it("tracks tool_end events and updates status", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      handler(makeToolEnd("call-1", "shell"));

      const tool = stream.getTool("call-1");
      expect(tool!.status).toBe("done");
      expect(tool!.elapsedMs).toBeGreaterThanOrEqual(0);
    });

    it("forwards non-tool events to callback", async () => {
      const forward = vi.fn();
      const stream = new ToolStream();
      stream.setStreamCallback(forward);
      const handler = stream.createStreamHandler();

      await handler(makeTextDelta("hello"));

      expect(forward).toHaveBeenCalled();
    });

    it("forwards tool events to callback", async () => {
      const forward = vi.fn();
      const stream = new ToolStream();
      stream.setStreamCallback(forward);
      const handler = stream.createStreamHandler();

      await handler(makeToolStart("call-1", "shell"));

      expect(forward).toHaveBeenCalledWith(expect.objectContaining({ type: "tool_start" }));
    });
  });

  describe("callbacks", () => {
    it("calls onToolStart when tool starts", () => {
      const onStart = vi.fn();
      const stream = new ToolStream({ onToolStart: onStart });
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));

      expect(onStart).toHaveBeenCalledWith("shell", "call-1");
    });

    it("calls onToolEnd when tool completes", () => {
      const onEnd = vi.fn();
      const stream = new ToolStream({ onToolEnd: onEnd });
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      handler(makeToolEnd("call-1", "shell"));

      expect(onEnd).toHaveBeenCalledWith("shell", "call-1", true, expect.any(Number));
    });
  });

  describe("errorTool", () => {
    it("marks tool as errored", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      stream.errorTool("call-1", "permission denied");

      const tool = stream.getTool("call-1");
      expect(tool!.status).toBe("error");
      expect(tool!.error).toBe("permission denied");
    });

    it("calls onToolError callback", () => {
      const onError = vi.fn();
      const stream = new ToolStream({ onToolError: onError });
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      stream.errorTool("call-1", "failed");

      expect(onError).toHaveBeenCalledWith("shell", "call-1", "failed");
    });
  });

  describe("getRunningTools", () => {
    it("returns only running tools", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      handler(makeToolStart("call-2", "read_file"));
      handler(makeToolEnd("call-1", "shell"));

      const running = stream.getRunningTools();
      expect(running).toHaveLength(1);
      expect(running[0]!.toolName).toBe("read_file");
    });
  });

  describe("getCompletedTools", () => {
    it("returns only done tools", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      handler(makeToolEnd("call-1", "shell"));
      handler(makeToolStart("call-2", "read_file"));

      const completed = stream.getCompletedTools();
      expect(completed).toHaveLength(1);
      expect(completed[0]!.toolName).toBe("shell");
    });
  });

  describe("getErroredTools", () => {
    it("returns only errored tools", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      stream.errorTool("call-1", "failed");
      handler(makeToolStart("call-2", "read_file"));

      const errored = stream.getErroredTools();
      expect(errored).toHaveLength(1);
      expect(errored[0]!.toolName).toBe("shell");
    });
  });

  describe("clear", () => {
    it("removes all tracked tools", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      stream.clear();

      expect(stream.getAllTools()).toHaveLength(0);
    });
  });

  describe("reset", () => {
    it("keeps in-progress tools, removes completed", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      handler(makeToolEnd("call-1", "shell"));
      handler(makeToolStart("call-2", "read_file"));

      stream.reset();

      const tools = stream.getAllTools();
      expect(tools).toHaveLength(1);
      expect(tools[0]!.toolCallId).toBe("call-2");
    });
  });

  describe("getToolCounts", () => {
    it("returns counts by status", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      handler(makeToolStart("call-2", "read_file"));
      handler(makeToolEnd("call-1", "shell"));
      stream.errorTool("call-2", "fail");

      const counts = stream.getToolCounts();
      expect(counts.running).toBe(0);
      expect(counts.done).toBe(1);
      expect(counts.error).toBe(1);
    });
  });

  describe("getTotalElapsedMs", () => {
    it("sums elapsed time for all tools", () => {
      const stream = new ToolStream();
      const handler = stream.createStreamHandler();

      handler(makeToolStart("call-1", "shell"));
      handler(makeToolEnd("call-1", "shell"));
      handler(makeToolStart("call-2", "read_file"));
      handler(makeToolEnd("call-2", "read_file"));

      const total = stream.getTotalElapsedMs();
      expect(total).toBeGreaterThanOrEqual(0);
    });
  });
});

describe("createToolStream", () => {
  it("creates with callbacks", () => {
    const onStart = vi.fn();
    const stream = createToolStream({ onToolStart: onStart });
    expect(stream).toBeInstanceOf(ToolStream);
  });
});