import { describe, it, expect } from "vitest";
import { redactRecord } from "../../src/infra/observability/redact.js";
import type { LogRecord } from "../../src/infra/observability/schema.js";

function makeRecord(overrides: Partial<LogRecord> = {}): LogRecord {
  return {
    ts: new Date().toISOString(),
    level: "info",
    module: "test",
    msg: overrides.msg ?? "test message",
    schemaVersion: 1,
    ...overrides,
  };
}

describe("redactRecord — bearer tokens", () => {
  it("replaces a Bearer token in fields with [REDACTED]", () => {
    const record = makeRecord({
      fields: { auth: "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abc12345" },
    });
    redactRecord(record as unknown as Record<string, unknown>, ["tokens"]);
    expect((record.fields as Record<string, string>).auth).toBe("<redacted:token>");
  });

  it("replaces a Bearer token in msg", () => {
    const record = makeRecord({
      msg: "Auth header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abc12345",
    });
    redactRecord(record as unknown as Record<string, unknown>, ["tokens"]);
    expect(record.msg).not.toContain("Bearer eyJ");
    expect(record.msg).toContain("<redacted:token>");
  });
});

describe("redactRecord — API keys", () => {
  it("replaces sk-... patterns in msg", () => {
    const record = makeRecord({
      msg: "Using key sk-abcdefghij1234567890xyz to call API",
    });
    redactRecord(record as unknown as Record<string, unknown>, ["tokens"]);
    expect(record.msg).not.toContain("sk-abcdefghij");
    expect(record.msg).toContain("<redacted:token>");
  });

  it("replaces sk-ant-... patterns in msg", () => {
    const record = makeRecord({
      msg: "Anthropic key: sk-ant-abcdefghijklmnopqrstuvwxyz1234567890",
    });
    redactRecord(record as unknown as Record<string, unknown>, ["tokens"]);
    expect(record.msg).toContain("<redacted:token>");
    expect(record.msg).not.toContain("sk-ant-abc");
  });

  it("replaces sk-... API key in fields", () => {
    const record = makeRecord({
      fields: { apiKey: "sk-testkey1234567890abcdefghij" },
    });
    redactRecord(record as unknown as Record<string, unknown>, ["tokens"]);
    expect((record.fields as Record<string, string>).apiKey).toBe("<redacted:token>");
  });
});

describe("redactRecord — emails", () => {
  it("redacts email address in fields", () => {
    const record = makeRecord({
      fields: { userEmail: "user@example.com" },
    });
    redactRecord(record as unknown as Record<string, unknown>, ["emails"]);
    expect((record.fields as Record<string, string>).userEmail).toBe("<redacted:email>");
  });

  it("redacts email in msg", () => {
    const record = makeRecord({ msg: "Sending to user@example.com now" });
    redactRecord(record as unknown as Record<string, unknown>, ["emails"]);
    expect(record.msg).toContain("<redacted:email>");
    expect(record.msg).not.toContain("user@example.com");
  });
});

describe("redactRecord — non-sensitive strings", () => {
  it("leaves non-sensitive msg unchanged", () => {
    const record = makeRecord({ msg: "Normal log message with no secrets" });
    redactRecord(record as unknown as Record<string, unknown>, ["tokens", "emails"]);
    expect(record.msg).toBe("Normal log message with no secrets");
  });

  it("leaves non-sensitive fields unchanged", () => {
    const record = makeRecord({ fields: { key: "harmless-value", count: 42 } });
    redactRecord(record as unknown as Record<string, unknown>, ["tokens", "emails"]);
    expect((record.fields as Record<string, unknown>).key).toBe("harmless-value");
    expect((record.fields as Record<string, unknown>).count).toBe(42);
  });
});

describe("redactRecord — err.message", () => {
  it("redacts token in err.message", () => {
    const record: Record<string, unknown> = makeRecord({
      msg: "An error occurred",
    }) as unknown as Record<string, unknown>;
    record.err = {
      name: "Error",
      message: "Failed with key sk-secretkey1234567890abcde",
    };
    redactRecord(record, ["tokens"]);
    const err = record.err as Record<string, string>;
    expect(err.message).toContain("<redacted:token>");
    expect(err.message).not.toContain("sk-secretkey");
  });

  it("redacts email in err.message", () => {
    const record: Record<string, unknown> = makeRecord({
      msg: "error",
    }) as unknown as Record<string, unknown>;
    record.err = {
      name: "Error",
      message: "Sending to admin@internal.org failed",
    };
    redactRecord(record, ["emails"]);
    const err = record.err as Record<string, string>;
    expect(err.message).toContain("<redacted:email>");
  });
});

describe("redactRecord — empty targets", () => {
  it("does nothing when targets array is empty", () => {
    const record = makeRecord({
      msg: "Bearer supersecrettoken1234567890abcde",
      fields: { email: "leaking@example.com" },
    });
    const originalMsg = record.msg;
    redactRecord(record as unknown as Record<string, unknown>, []);
    expect(record.msg).toBe(originalMsg);
    expect((record.fields as Record<string, string>).email).toBe("leaking@example.com");
  });
});
