import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { StructuredOutputManager, createStructuredOutput, isJsonModeEnabled, isQuietModeEnabled } from "../../src/cli/structured-output.js";

describe("StructuredOutputManager", () => {
  let manager: StructuredOutputManager;

  beforeEach(() => {
    manager = new StructuredOutputManager();
    delete process.env.STACKOWL_JSON;
    delete process.env.STACKOWL_NO_TUI;
    delete process.env.STACKOWL_QUIET;
  });

  afterEach(() => {
    delete process.env.STACKOWL_JSON;
    delete process.env.STACKOWL_NO_TUI;
    delete process.env.STACKOWL_QUIET;
  });

  describe("isActive", () => {
    it("defaults to false without flags", () => {
      expect(manager.isActive()).toBe(false);
    });

    it("enables when STACKOWL_JSON is set", () => {
      process.env.STACKOWL_JSON = "true";
      const m = new StructuredOutputManager();
      expect(m.isActive()).toBe(true);
    });
  });

  describe("success", () => {
    it("creates ok status output", () => {
      const output = manager.success("test content", { owlName: "Noctua" });
      expect(output.status).toBe("ok");
      expect(output.content).toBe("test content");
      expect(output.owlName).toBe("Noctua");
      expect(output.timestamp).toBeDefined();
    });

    it("includes usage when provided", () => {
      const usage = { promptTokens: 100, completionTokens: 50, totalTokens: 150 };
      const output = manager.success("test", { usage });
      expect(output.usage).toEqual(usage);
    });
  });

  describe("error", () => {
    it("creates error status output", () => {
      const output = manager.error("something failed", "ERR_CODE");
      expect(output.status).toBe("error");
      expect(output.error).toBe("something failed");
      expect(output.code).toBe("ERR_CODE");
    });
  });

  describe("print", () => {
    it("writes JSON to stdout when active", () => {
      process.env.STACKOWL_JSON = "true";
      const m = new StructuredOutputManager();
      const output = m.success("hello");
      
      let written = "";
      const origWrite = process.stdout.write;
      process.stdout.write = (s: string) => { written = s; return true; };
      
      m.print(output);
      
      expect(written).toContain('"status":"ok"');
      expect(written).toContain('"content":"hello"');
      
      process.stdout.write = origWrite;
    });

    it("does not print to stdout when inactive", () => {
      const output = manager.success("hello");
      
      let called = false;
      const origWrite = process.stdout.write;
      process.stdout.write = () => { called = true; return true; };
      
      manager.print(output);
      
      expect(called).toBe(false);
      process.stdout.write = origWrite;
    });
  });

  describe("printError", () => {
    it("writes error JSON to stderr when active", () => {
      process.env.STACKOWL_JSON = "true";
      const m = new StructuredOutputManager();
      const output = m.error("fail");
      
      let written = "";
      const origWrite = process.stderr.write;
      process.stderr.write = (s: string) => { written = s; return true; };
      
      m.printError(output);
      
      expect(written).toContain('"status":"error"');
      expect(written).toContain('"error":"fail"');
      
      process.stderr.write = origWrite;
    });
  });

  describe("shouldSuppressTui", () => {
    it("returns true when STACKOWL_NO_TUI is set", () => {
      process.env.STACKOWL_NO_TUI = "true";
      const m = new StructuredOutputManager();
      expect(m.shouldSuppressTui()).toBe(true);
    });

    it("returns true in json mode", () => {
      process.env.STACKOWL_JSON = "true";
      const m = new StructuredOutputManager();
      expect(m.shouldSuppressTui()).toBe(true);
    });
  });

  describe("formatCommandOutput", () => {
    it("adds command and duration", () => {
      const output = manager.success("result");
      const cmdOutput = manager.formatCommandOutput("status", output);

      expect(cmdOutput.command).toBe("status");
      expect(cmdOutput.durationMs).toBeGreaterThanOrEqual(0);
    });
  });

  describe("queue and flush", () => {
    it("queues outputs", () => {
      process.env.STACKOWL_JSON = "true";
      const m = new StructuredOutputManager();

      m.queue(m.success("one"));
      m.queue(m.success("two"));
      
      expect(m["outputs"]).toHaveLength(2);
    });
  });
});

describe("isJsonModeEnabled", () => {
  it("returns false when no env or args", () => {
    delete process.env.STACKOWL_JSON;
    expect(isJsonModeEnabled()).toBe(false);
  });

  it("returns true when STACKOWL_JSON is set", () => {
    process.env.STACKOWL_JSON = "true";
    expect(isJsonModeEnabled()).toBe(true);
    delete process.env.STACKOWL_JSON;
  });
});

describe("isQuietModeEnabled", () => {
  it("returns true when STACKOWL_QUIET is set", () => {
    process.env.STACKOWL_QUIET = "true";
    expect(isQuietModeEnabled()).toBe(true);
    delete process.env.STACKOWL_QUIET;
  });
});