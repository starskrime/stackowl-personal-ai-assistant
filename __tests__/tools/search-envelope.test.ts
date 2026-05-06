import { describe, it, expect, vi, afterEach } from "vitest";
import { DuckDuckGoSearchTool } from "../../src/tools/search.js";
import { parseWebToolResult } from "../../src/browser/envelope.js";

const originalFetch = globalThis.fetch;

describe("search.ts — BlockingClassifier wired (Element 16c)", () => {
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("invokes BlockingClassifier instead of hardcoded keyword list when 0 results parsed", async () => {
    const classify = vi.fn().mockResolvedValue({ blocked: true, reason: "captcha", confidence: 0.9, source: "router" });
    const fetchSpy = vi.fn().mockResolvedValue(new Response("<html>verify you are human</html>", { status: 200 }));
    global.fetch = fetchSpy as any;
    const result = await DuckDuckGoSearchTool.execute(
      { query: "x" },
      { classifier: { classify } as any } as any,
    );
    expect(classify).toHaveBeenCalledOnce();
    // The result on block should be a JSON string with success:false and code BLOCKED_BY_ANTI_BOT
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("BLOCKED_BY_ANTI_BOT");
    expect(parsed.error.suggestedEscalation).toBe("live_browser");
  });
});

describe("search.ts envelope return", () => {
  it("returns WebToolResult JSON with kind:'search' on success", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(`<a class="result__a" href="https://example.com">Title</a><a class="result__snippet">Snip</a>`, { status: 200 }),
    ) as any;
    const out = await DuckDuckGoSearchTool.execute({ query: "ok" }, {} as any);
    const env = parseWebToolResult(out);
    expect(env).not.toBeNull();
    expect(env?.success).toBe(true);
    if (env?.success && env.data.kind === "search") {
      expect(env.data.query).toBe("ok");
      expect(Array.isArray(env.data.results)).toBe(true);
    }
  });
});
