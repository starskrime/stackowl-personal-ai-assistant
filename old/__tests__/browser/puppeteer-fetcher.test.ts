import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";

describe("PuppeteerFetcher", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("probe() returns true when executablePath resolves to existing file", async () => {
    vi.doMock("puppeteer", () => ({
      default: { use: vi.fn(), launch: vi.fn() },
      executablePath: () => "/fake/chrome",
    }));
    vi.doMock("node:fs", () => ({
      existsSync: () => true,
    }));
    const { PuppeteerFetcher } = await import("../../src/browser/puppeteer-fetcher.js");
    const f = new PuppeteerFetcher();
    expect(await f.probe()).toBe(true);
  });

  it("probe() returns false when executablePath throws", async () => {
    vi.doMock("puppeteer", () => ({
      default: { use: vi.fn(), launch: vi.fn() },
      executablePath: () => {
        throw new Error("not found");
      },
    }));
    vi.doMock("node:fs", () => ({
      existsSync: () => false,
    }));
    const { PuppeteerFetcher } = await import("../../src/browser/puppeteer-fetcher.js");
    const f = new PuppeteerFetcher();
    expect(await f.probe()).toBe(false);
  });

  it("close() sets browser and sessionPool to null", async () => {
    const { PuppeteerFetcher } = await import("../../src/browser/puppeteer-fetcher.js");
    const f = new PuppeteerFetcher();
    // Manually set private fields to mocks so close() can call them
    (f as any).browser = { close: vi.fn().mockResolvedValue(undefined), connected: true };
    (f as any).sessionPool = { teardown: vi.fn().mockResolvedValue(undefined) };
    await f.close();
    expect((f as any).browser).toBeNull();
    expect((f as any).sessionPool).toBeNull();
  });
});
