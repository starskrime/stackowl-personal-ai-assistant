import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { searchEnvelope, type SearchEnvelopeDeps } from "../../src/browser/smart-search.js";

const NOOP_BUS = { emit: () => {} } as any;

/** Classifier that detects CAPTCHA/block pages when bodyPreview contains "CAPTCHA" */
const CAPTCHA_CLASSIFIER = {
  classify: vi.fn().mockImplementation(({ bodyPreview }: { bodyPreview: string }) =>
    Promise.resolve({
      blocked: /captcha/i.test(bodyPreview),
      reason: "captcha",
      confidence: 0.95,
      source: "router",
    }),
  ),
};

let originalFetch: typeof globalThis.fetch;

beforeEach(() => {
  originalFetch = globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function makeFetchMock(opts: {
  ddgBlocked?: boolean;
  tavilyOk?: boolean;
  tavilyError?: boolean;
}) {
  return vi.fn().mockImplementation((url: string) => {
    if (String(url).includes("duckduckgo")) {
      if (opts.ddgBlocked) {
        return Promise.resolve({ ok: true, text: async () => "<html>CAPTCHA</html>" });
      }
      return Promise.resolve({
        ok: true,
        text: async () =>
          '<a class="result__a" href="https://ddg-result.com">DDG Title</a> <a class="result__snippet">DDG Snippet</a>',
      });
    }
    if (String(url).includes("tavily")) {
      if (opts.tavilyError) {
        return Promise.resolve({ ok: false, status: 401, json: async () => ({}) });
      }
      if (opts.tavilyOk) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            results: [{ title: "Tavily Result", url: "https://tavily-result.com", content: "Tavily snippet" }],
          }),
        });
      }
    }
    return Promise.resolve({ ok: false, status: 500, text: async () => "", json: async () => ({}) });
  });
}

describe("search escalation — DDG succeeds", () => {
  it("returns DDG results and does not call Tavily", async () => {
    const fetchMock = makeFetchMock({ ddgBlocked: false });
    global.fetch = fetchMock as any;

    const deps: SearchEnvelopeDeps = {
      tavilyApiKey: "test-key",
      bus: NOOP_BUS,
      jitterFn: () => Promise.resolve(),
    };
    const result = await searchEnvelope("best coffee", 5, deps);

    expect(result.success).toBe(true);
    if (result.success && result.data.kind === "search") {
      expect(result.data.results[0].url).toBe("https://ddg-result.com");
    }
    const tavilyCalls = fetchMock.mock.calls.filter(([url]: any[]) => String(url).includes("tavily"));
    expect(tavilyCalls).toHaveLength(0);
  });
});

describe("search escalation — DDG blocked → Tavily succeeds", () => {
  it("uses Tavily results when DDG is blocked", async () => {
    global.fetch = makeFetchMock({ ddgBlocked: true, tavilyOk: true }) as any;

    const deps: SearchEnvelopeDeps = {
      tavilyApiKey: "test-key",
      classifier: CAPTCHA_CLASSIFIER,
      bus: NOOP_BUS,
      jitterFn: () => Promise.resolve(),
    };
    const result = await searchEnvelope("blocked query", 5, deps);

    expect(result.success).toBe(true);
    if (result.success && result.data.kind === "search") {
      expect(result.data.results[0].title).toBe("Tavily Result");
    }
  });
});

describe("search escalation — CamoFox unavailable → Puppeteer used", () => {
  it("skips unavailable CamoFox and uses Puppeteer", async () => {
    global.fetch = makeFetchMock({ ddgBlocked: true, tavilyError: true }) as any;

    const mockPuppeteer = {
      probe: vi.fn().mockResolvedValue(true),
      fetch: vi.fn().mockResolvedValue({
        html: `<html><body><div class="g"><h3><a href="https://puppeteer-result.com">Puppeteer Result</a></h3></div></body></html>`,
        finalUrl: "https://www.google.com/search?q=test",
        status: 200,
      }),
    };
    const mockCamoFox = {
      isHealthy: vi.fn().mockResolvedValue(false),
    };

    const deps: SearchEnvelopeDeps = {
      tavilyApiKey: "test-key",
      classifier: CAPTCHA_CLASSIFIER,
      camofox: mockCamoFox as any,
      puppeteer: mockPuppeteer as any,
      bus: NOOP_BUS,
      jitterFn: () => Promise.resolve(),
    };
    const result = await searchEnvelope("test query", 5, deps);

    expect(result.success).toBe(true);
    if (result.success && result.data.kind === "search") {
      expect(result.data.results[0].url).toBe("https://puppeteer-result.com");
    }
    expect(mockCamoFox.isHealthy).toHaveBeenCalled();
    expect(mockPuppeteer.fetch).toHaveBeenCalledWith(
      expect.stringContaining("google.com/search"),
      expect.objectContaining({ waitForSelector: "div.g" }),
    );
  });
});

describe("search escalation — all tiers fail", () => {
  it("returns BLOCKED_BY_ANTI_BOT with suggestedEscalation: live_browser", async () => {
    global.fetch = makeFetchMock({ ddgBlocked: true, tavilyError: true }) as any;

    const mockPuppeteer = {
      probe: vi.fn().mockResolvedValue(true),
      fetch: vi.fn().mockResolvedValue({
        html: "<html><body>CAPTCHA</body></html>",
        finalUrl: "https://www.google.com/search?q=test",
        status: 200,
      }),
    };
    const mockCamoFox = {
      isHealthy: vi.fn().mockResolvedValue(true),
      createTab: vi.fn().mockResolvedValue({ tabId: "t1", snapshot: "", refs: {}, url: "" }),
      navigate: vi.fn().mockResolvedValue({ snapshot: "CAPTCHA page", refs: {}, url: "" }),
      closeTab: vi.fn().mockResolvedValue(undefined),
    };

    const deps: SearchEnvelopeDeps = {
      tavilyApiKey: "test-key",
      classifier: CAPTCHA_CLASSIFIER,
      camofox: mockCamoFox as any,
      puppeteer: mockPuppeteer as any,
      bus: NOOP_BUS,
      jitterFn: () => Promise.resolve(),
    };
    const result = await searchEnvelope("fail query", 5, deps);

    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.code).toBe("BLOCKED_BY_ANTI_BOT");
      expect(result.error.attemptedTiers.length).toBe(4);
      expect(result.error.suggestedEscalation).toBe("live_browser");
    }
  });
});
