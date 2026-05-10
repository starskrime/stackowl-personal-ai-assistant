/**
 * StackOwl — Element 7 T20 — Production ChromeBackend
 *
 * Adapts the existing `BrowserBridge` (puppeteer-managed CDP attachment) to
 * the slim `ChromeBackend` interface ChromeDriver depends on. This file is
 * the only one that imports puppeteer types in the live_browser path —
 * keeping `chrome-driver.ts` puppeteer-free makes unit tests fast.
 *
 * Active-page bookkeeping lives here: puppeteer pages do not track which
 * one was last activated, so we remember it after `activateTab` and fall
 * back to the bridge's tracked page otherwise.
 */
import type { Page } from "puppeteer";
import { BrowserBridge } from "../computer-use/browser/cdp.js";
import type { ChromeBackend, PageLike } from "./chrome-driver.js";
import { log } from "../../logger.js";

/**
 * Adapt a puppeteer `Page` to the structural `PageLike` interface
 * ChromeDriver expects. The shape lines up 1:1 — this is a type-narrowing
 * shim, not a wrapper, so calls fall straight through.
 */
function asPageLike(p: Page): PageLike {
  return {
    title: () => p.title(),
    url: () => p.url(),
    goto: async (url) => {
      await p.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
    },
    click: (selector) => p.click(selector),
    type: (selector, value) => p.type(selector, value),
    evaluate: <T>(fn: (...args: unknown[]) => T, ...args: unknown[]) =>
      // Puppeteer's evaluate has the same calling convention.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (p.evaluate as any)(fn, ...args),
    bringToFront: () => p.bringToFront(),
    close: () => p.close(),
    goBack: async () => {
      await p.goBack();
    },
    goForward: async () => {
      await p.goForward();
    },
  };
}

export class PuppeteerChromeBackend implements ChromeBackend {
  private active: Page | null = null;

  constructor(private readonly bridge: BrowserBridge = BrowserBridge.getInstance()) {}

  private requireBrowser() {
    // BrowserBridge stores the puppeteer Browser privately; use isConnected
    // as a precondition guard. Production callers must connect/launch first.
    if (!this.bridge.isConnected()) {
      throw new Error(
        "Chrome is not connected. Run live_browser bootstrap (or browser_connect) first.",
      );
    }
  }

  private async allPages(): Promise<Page[]> {
    this.requireBrowser();
    // The bridge exposes pages via its internal Browser; reach through it.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const browser = (this.bridge as any).browser as { pages(): Promise<Page[]> } | null;
    if (!browser) return [];
    return browser.pages();
  }

  async pages(): Promise<PageLike[]> {
    log.tool.debug("chrome-backend.pages: entry");
    const ps = await this.allPages();
    log.tool.debug("chrome-backend.pages: exit", { pageCount: ps.length });
    return ps.map(asPageLike);
  }

  async activePage(): Promise<PageLike> {
    log.tool.debug("chrome-backend.activePage: entry");
    this.requireBrowser();
    const ps = await this.allPages();
    if (this.active && ps.includes(this.active)) {
      log.tool.debug("chrome-backend.activePage: returning tracked active page");
      return asPageLike(this.active);
    }
    const fallback = ps[0];
    if (!fallback) throw new Error("No open pages in Chrome.");
    this.active = fallback;
    log.tool.debug("chrome-backend.activePage: returning fallback page", { url: fallback.url() });
    return asPageLike(fallback);
  }

  async newPage(url?: string): Promise<PageLike> {
    log.tool.debug("chrome-backend.newPage: entry", { url });
    this.requireBrowser();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const browser = (this.bridge as any).browser as {
      newPage(): Promise<Page>;
    } | null;
    if (!browser) throw new Error("Chrome browser handle missing.");
    const page = await browser.newPage();
    if (url) {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
    }
    this.active = page;
    log.tool.debug("chrome-backend.newPage: exit", { url });
    return asPageLike(page);
  }

  async activateTab(index: number): Promise<void> {
    log.tool.debug("chrome-backend.activateTab: entry", { index });
    const ps = await this.allPages();
    const target = ps[index];
    if (!target) {
      log.tool.debug("chrome-backend.activateTab: index out of range", { index, pageCount: ps.length });
      return;
    }
    await target.bringToFront();
    this.active = target;
    log.tool.debug("chrome-backend.activateTab: exit", { index });
  }
}
