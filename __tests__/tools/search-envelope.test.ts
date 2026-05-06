import { describe, it, expect, vi } from "vitest";
import { DuckDuckGoSearchTool } from "../../src/tools/search.js";

describe("search.ts — BlockingClassifier wired (Element 16c)", () => {
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
