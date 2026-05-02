// __tests__/tools/tool-error.test.ts
import { describe, it, expect } from "vitest";
import { toolError, toolSuccess } from "../../src/tools/tool-error.js";

describe("toolError", () => {
  it("produces { success: false, error: { code, message } }", () => {
    const out = JSON.parse(toolError("MISSING_ARG", "field x is required"));
    expect(out.success).toBe(false);
    expect(out.error.code).toBe("MISSING_ARG");
    expect(out.error.message).toBe("field x is required");
  });

  it("includes suggestion when provided", () => {
    const out = JSON.parse(toolError("NOT_FOUND", "file missing", "check the path"));
    expect(out.error.suggestion).toBe("check the path");
  });

  it("omits suggestion when not provided", () => {
    const out = JSON.parse(toolError("ERR", "msg"));
    expect(out.error).not.toHaveProperty("suggestion");
  });
});

describe("toolSuccess", () => {
  it("produces { success: true, data: <value> }", () => {
    const out = JSON.parse(toolSuccess({ x: 1, y: "hello" }));
    expect(out.success).toBe(true);
    expect(out.data.x).toBe(1);
    expect(out.data.y).toBe("hello");
  });

  it("works with primitive data", () => {
    const out = JSON.parse(toolSuccess("plain text result"));
    expect(out.success).toBe(true);
    expect(out.data).toBe("plain text result");
  });
});
