/**
 * Integration: JSONL log reader with a real temp file.
 *
 * Writes a known set of records to a temporary file, then asserts that
 * readLogsArray filters them correctly by various criteria.
 */

import { writeFileSync, mkdirSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { readLogsArray } from "../../../src/infra/observability/reader.js";
import type { LogRecord } from "../../../src/infra/observability/schema.js";

// ── Temp directory & fixture data ────────────────────────────────────

const TMP_DIR = join(tmpdir(), `stackowl-reader-test-${process.pid}`);
const LOG_FILE = join(TMP_DIR, "stackowl-2025-01-01.log");

const TRACE_A = "a".repeat(32);
const TRACE_B = "b".repeat(32);

/** Build a minimal-but-valid LogRecord. */
function makeRecord(overrides: Partial<LogRecord>): LogRecord {
  return {
    ts:            "2025-01-01T12:00:00.000Z",
    level:         "info",
    module:        "engine",
    msg:           "test message",
    schemaVersion: 1,
    ...overrides,
  };
}

/** All 10 fixture records written to the temp log file. */
const FIXTURES: LogRecord[] = [
  // 0 — debug, engine
  makeRecord({ level: "debug",  module: "engine",  msg: "debug engine message",    traceId: TRACE_A }),
  // 1 — info, engine
  makeRecord({ level: "info",   module: "engine",  msg: "engine started",          traceId: TRACE_A }),
  // 2 — info, gateway
  makeRecord({ level: "info",   module: "gateway", msg: "request received",         traceId: TRACE_A }),
  // 3 — warn, gateway
  makeRecord({ level: "warn",   module: "gateway", msg: "slow keyword response",    traceId: TRACE_B }),
  // 4 — error, engine
  makeRecord({ level: "error",  module: "engine",  msg: "unexpected error occurred",traceId: TRACE_B }),
  // 5 — fatal, gateway
  makeRecord({ level: "fatal",  module: "gateway", msg: "fatal crash",              traceId: TRACE_B }),
  // 6 — info, tool.read_logs
  makeRecord({ level: "info",   module: "tool.read_logs", msg: "reading logs",      traceId: TRACE_A }),
  // 7 — debug, parliament
  makeRecord({ level: "debug",  module: "parliament", msg: "parliament debug",      traceId: TRACE_B }),
  // 8 — warn, engine
  makeRecord({ level: "warn",   module: "engine",  msg: "engine warning keyword",   traceId: TRACE_A }),
  // 9 — error, parliament
  makeRecord({ level: "error",  module: "parliament", msg: "parliament error",      traceId: TRACE_B }),
];

beforeAll(() => {
  mkdirSync(TMP_DIR, { recursive: true });
  const lines = FIXTURES.map((r) => JSON.stringify(r)).join("\n");
  writeFileSync(LOG_FILE, lines + "\n", "utf8");
});

afterAll(() => {
  rmSync(TMP_DIR, { recursive: true, force: true });
});

// ── Tests ─────────────────────────────────────────────────────────────

describe("readLogsArray — filter: errorOnly", () => {
  it("returns only error and fatal records when errorOnly: true", async () => {
    const results = await readLogsArray(TMP_DIR, { errorOnly: true });

    expect(results.length).toBeGreaterThan(0);
    for (const r of results) {
      expect(["error", "fatal"]).toContain(r.level);
    }

    // Sanity: the three error/fatal fixtures are all present.
    const msgs = results.map((r) => r.msg);
    expect(msgs).toContain("unexpected error occurred");
    expect(msgs).toContain("fatal crash");
    expect(msgs).toContain("parliament error");
  });
});

describe("readLogsArray — filter: module", () => {
  it("returns only records from the gateway module", async () => {
    const results = await readLogsArray(TMP_DIR, { module: "gateway" });

    expect(results.length).toBeGreaterThan(0);
    for (const r of results) {
      expect(r.module).toBe("gateway");
    }

    const msgs = results.map((r) => r.msg);
    expect(msgs).toContain("request received");
    expect(msgs).toContain("slow keyword response");
    expect(msgs).toContain("fatal crash");
  });
});

describe("readLogsArray — filter: traceId", () => {
  it("returns only records with the specified traceId", async () => {
    const results = await readLogsArray(TMP_DIR, { traceId: TRACE_A });

    expect(results.length).toBeGreaterThan(0);
    for (const r of results) {
      expect(r.traceId).toBe(TRACE_A);
    }

    // TRACE_B records must not appear.
    const traceBCount = results.filter((r) => r.traceId === TRACE_B).length;
    expect(traceBCount).toBe(0);
  });
});

describe("readLogsArray — filter: limit", () => {
  it("returns at most 3 records when limit: 3", async () => {
    // Query without any other filters so there are plenty of candidates.
    const results = await readLogsArray(TMP_DIR, { limit: 3, level: "debug" });

    expect(results.length).toBeLessThanOrEqual(3);
  });
});

describe("readLogsArray — filter: contains", () => {
  it("returns only records whose msg contains the keyword (case-insensitive)", async () => {
    const results = await readLogsArray(TMP_DIR, { contains: "keyword", level: "debug" });

    expect(results.length).toBeGreaterThan(0);
    for (const r of results) {
      expect(r.msg.toLowerCase()).toContain("keyword");
    }

    // Fixtures with "keyword": indexes 3 and 8.
    const msgs = results.map((r) => r.msg);
    expect(msgs).toContain("slow keyword response");
    expect(msgs).toContain("engine warning keyword");
  });
});

describe("readLogsArray — filter: level", () => {
  it("returns only warn+ records when level: warn", async () => {
    const results = await readLogsArray(TMP_DIR, { level: "warn" });

    expect(results.length).toBeGreaterThan(0);
    for (const r of results) {
      expect(["warn", "error", "fatal"]).toContain(r.level);
    }

    // debug and info records must NOT appear.
    const tooLow = results.filter((r) => r.level === "debug" || r.level === "info");
    expect(tooLow).toHaveLength(0);
  });
});
