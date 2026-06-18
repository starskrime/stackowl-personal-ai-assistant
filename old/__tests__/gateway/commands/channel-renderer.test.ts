import { describe, it, expect } from "vitest";
import {
  renderForTelegram,
  renderAsPlainText,
  renderAsHtml,
} from "../../../src/gateway/commands/channel-renderer.js";
import type { CoreCommandResult } from "../../../src/cli/v2/commands/registry.js";

describe("renderForTelegram", () => {
  it("renders system-message with MarkdownV2 escaping", () => {
    const r: CoreCommandResult = { kind: "system-message", text: "hello world" };
    const out = renderForTelegram(r);
    expect(out).toContain("hello");
    expect(out).not.toContain("❌");
  });

  it("escapes MarkdownV2 special chars in error", () => {
    const r: CoreCommandResult = { kind: "error", text: "Provider (ollama) not found." };
    const out = renderForTelegram(r);
    expect(out).toContain("❌");
    expect(out).not.toContain("(ollama)"); // parens must be escaped
    expect(out).toContain("\\(ollama\\)");
  });

  it("renders action as empty string", () => {
    expect(renderForTelegram({ kind: "action" })).toBe("");
  });

  it("bolds key-value pairs in system-message", () => {
    const r: CoreCommandResult = { kind: "system-message", text: "port: 3077\nhost: 127.0.0.1" };
    const out = renderForTelegram(r);
    expect(out).toContain("*port:*");
    expect(out).toContain("*host:*");
  });
});

describe("renderAsPlainText", () => {
  it("returns text for system-message", () => {
    expect(renderAsPlainText({ kind: "system-message", text: "ok" })).toBe("ok");
  });

  it("prefixes error with 'Error:'", () => {
    expect(renderAsPlainText({ kind: "error", text: "bad" })).toBe("Error: bad");
  });

  it("returns empty string for action", () => {
    expect(renderAsPlainText({ kind: "action" })).toBe("");
  });
});

describe("renderAsHtml", () => {
  it("wraps system-message in pre tag", () => {
    const out = renderAsHtml({ kind: "system-message", text: "ok" });
    expect(out).toContain("<pre>");
    expect(out).toContain("</pre>");
  });

  it("escapes HTML entities in system-message", () => {
    const out = renderAsHtml({ kind: "system-message", text: "<b>bold</b>" });
    expect(out).toContain("&lt;b&gt;");
    expect(out).not.toContain("<b>");
  });

  it("prefixes error with emoji and bold tag", () => {
    const out = renderAsHtml({ kind: "error", text: "bad" });
    expect(out).toContain("❌");
    expect(out).toContain("<b>");
  });
});
