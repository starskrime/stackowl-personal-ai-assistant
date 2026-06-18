import { describe, it, expect, beforeAll, afterAll, beforeEach, afterEach } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { installTestSink, capturedLogs, clearTestSink } from "../../src/infra/observability/sinks/test-sink.js";
import { runWithContext } from "../../src/infra/observability/context.js";
import { randomTraceId } from "../../src/infra/observability/ids.js";
import { setMinLevel } from "../../src/infra/observability/logger.js";
import type { ToolContext } from "../../src/tools/registry.js";

describe("ToolRegistry span instrumentation", () => {
  let registry: ToolRegistry;

  beforeAll(() => {
    setMinLevel("debug");
  });

  afterAll(() => setMinLevel("info"));

  beforeEach(() => {
    installTestSink();
    registry = new ToolRegistry();
    registry.register({
      definition: {
        name: "echo",
        description: "test tool",
        parameters: { type: "object", properties: { msg: { type: "string" } }, required: ["msg"] },
      },
      execute: async (args) => `echo: ${args.msg}`,
    });
  });

  afterEach(() => clearTestSink());

  it("emits toolCall record with tool name and args", async () => {
    const traceId = randomTraceId();
    await runWithContext({ traceId }, () =>
      registry.execute("echo", { msg: "hello" }, { cwd: "/tmp" } as ToolContext)
    );
    const calls = capturedLogs().filter(r => r.msg?.includes("tool.call"));
    expect(calls.length).toBeGreaterThan(0);
    expect(calls[0].fields?.tool).toBe("echo");
    expect(calls[0].traceId).toBe(traceId);
  });

  it("emits toolResult record with success flag", async () => {
    await runWithContext({}, () =>
      registry.execute("echo", { msg: "hi" }, { cwd: "/tmp" } as ToolContext)
    );
    const results = capturedLogs().filter(r => r.msg?.includes("tool.result"));
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].fields?.success).toBe(true);
  });

  it("emits error record when tool throws", async () => {
    registry.register({
      definition: { name: "boom", description: "fails", parameters: { type: "object", properties: {} } },
      execute: async () => { throw new Error("tool exploded"); },
    });
    await expect(
      runWithContext({}, () => registry.execute("boom", {}, { cwd: "/tmp" } as ToolContext))
    ).rejects.toThrow();
    const errors = capturedLogs().filter(r => r.level === "error");
    expect(errors.length).toBeGreaterThan(0);
    expect(errors[0].err?.message).toContain("tool exploded");
  });
});
