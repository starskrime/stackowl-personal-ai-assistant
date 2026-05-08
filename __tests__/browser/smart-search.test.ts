import { describe, it, expect, vi } from "vitest";
import {
  createDdgHtmlTier,
  createTavilyApiTier,
  createGoogleCamoFoxTier,
  createGooglePuppeteerTier,
  searchEnvelope,
  type SearchEnvelopeDeps,
} from "../../src/browser/smart-search.js";

// ─── Tier identity tests ────────────────────────────────────────

describe("createDdgHtmlTier", () => {
  it("has tier:1 and name:'scrapling'", () => {
    const t = createDdgHtmlTier();
    expect(t.tier).toBe(1);
    expect(t.name).toBe("scrapling");
  });

  it("isAvailable() returns true", async () => {
    expect(await createDdgHtmlTier().isAvailable()).toBe(true);
  });
});

describe("createTavilyApiTier", () => {
  it("has tier:2 and name:'tavily-api'", () => {
    const t = createTavilyApiTier("test-key");
    expect(t.tier).toBe(2);
    expect(t.name).toBe("tavily-api");
  });

  it("isAvailable() returns true (key checked at construction)", async () => {
    expect(await createTavilyApiTier("k").isAvailable()).toBe(true);
  });

  it("maps Tavily json.results to SearchResult[]", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [{ title: "Test Title", url: "https://example.com", content: "Snippet" }],
      }),
    }) as any;
    const t = createTavilyApiTier("key");
    const result = await t.run("best coffee", { bus: { emit: () => {} } as any });
    expect(result.attempt.outcome).toBe("success");
    expect(result.data?.kind).toBe("search");
    if (result.data?.kind === "search") {
      expect(result.data.results[0]).toMatchObject({ title: "Test Title", url: "https://example.com" });
    }
    vi.restoreAllMocks();
  });
});

describe("createGoogleCamoFoxTier", () => {
  it("has tier:3 and name:'google-camofox'", () => {
    const client = { isHealthy: vi.fn().mockResolvedValue(true) } as any;
    const t = createGoogleCamoFoxTier(client);
    expect(t.tier).toBe(3);
    expect(t.name).toBe("google-camofox");
  });

  it("isAvailable() delegates to client.isHealthy()", async () => {
    const healthy = { isHealthy: vi.fn().mockResolvedValue(true) } as any;
    const unhealthy = { isHealthy: vi.fn().mockResolvedValue(false) } as any;
    expect(await createGoogleCamoFoxTier(healthy).isAvailable()).toBe(true);
    expect(await createGoogleCamoFoxTier(unhealthy).isAvailable()).toBe(false);
  });
});

describe("createGooglePuppeteerTier", () => {
  it("has tier:4 and name:'google-puppeteer'", () => {
    const fetcher = { probe: vi.fn().mockResolvedValue(true) } as any;
    const t = createGooglePuppeteerTier(fetcher);
    expect(t.tier).toBe(4);
    expect(t.name).toBe("google-puppeteer");
  });

  it("isAvailable() delegates to fetcher.probe()", async () => {
    const ready = { probe: vi.fn().mockResolvedValue(true) } as any;
    const notReady = { probe: vi.fn().mockResolvedValue(false) } as any;
    expect(await createGooglePuppeteerTier(ready).isAvailable()).toBe(true);
    expect(await createGooglePuppeteerTier(notReady).isAvailable()).toBe(false);
  });
});

// ─── searchEnvelope() tests ───────────────────────────────────────

describe("searchEnvelope tier inclusion", () => {
  it("omits Tier 2 when deps.tavilyApiKey is undefined", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () =>
        '<a class="result__a" href="https://example.com">Title</a> <a class="result__snippet">Snippet</a>',
    }) as any;
    const deps: SearchEnvelopeDeps = {
      bus: { emit: () => {} } as any,
    };
    await searchEnvelope("test query", 5, deps);
    const calls = (global.fetch as any).mock.calls as Array<[string]>;
    const tavilyCalls = calls.filter(([url]) => String(url).includes("tavily"));
    expect(tavilyCalls).toHaveLength(0);
    vi.restoreAllMocks();
  });

  it("omits Tier 3 when deps.camofox is undefined", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () =>
        '<a class="result__a" href="https://example.com">Title</a>',
    }) as any;
    const deps: SearchEnvelopeDeps = { bus: { emit: () => {} } as any };
    // no camofox — if CamoFox tier were built it would call client.isHealthy() and throw
    await searchEnvelope("test", 5, deps);
    vi.restoreAllMocks();
  });

  it("omits Tier 4 when deps.puppeteer is undefined", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () =>
        '<a class="result__a" href="https://example.com">Title</a>',
    }) as any;
    const deps: SearchEnvelopeDeps = { bus: { emit: () => {} } as any };
    await searchEnvelope("test", 5, deps);
    vi.restoreAllMocks();
  });

  it("returns Tavily results when DDG returns blocked", async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("duckduckgo")) {
        return Promise.resolve({ ok: true, text: async () => "<html>CAPTCHA page</html>" });
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          results: [{ title: "Tavily Result", url: "https://tavily-result.com", content: "From Tavily" }],
        }),
      });
    }) as any;

    // Classifier identifies CAPTCHA pages as blocked so DDG escalates to Tavily
    const classifier = {
      classify: vi.fn().mockResolvedValue({ blocked: true, reason: "captcha", confidence: 0.95, source: "router" }),
    };
    const deps: SearchEnvelopeDeps = {
      tavilyApiKey: "test-key",
      classifier,
      bus: { emit: () => {} } as any,
    };
    const result = await searchEnvelope("blocked query", 5, deps);
    expect(result.success).toBe(true);
    if (result.success && result.data.kind === "search") {
      expect(result.data.results[0].title).toBe("Tavily Result");
    }
    vi.restoreAllMocks();
  });

  it("sets suggestedEscalation to 'live_browser' when all tiers fail", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 429,
      text: async () => "",
    }) as any;
    const deps: SearchEnvelopeDeps = { bus: { emit: () => {} } as any };
    const result = await searchEnvelope("impossible query", 5, deps);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.suggestedEscalation).toBe("live_browser");
    }
    vi.restoreAllMocks();
  });
});
