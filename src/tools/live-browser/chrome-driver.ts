/**
 * StackOwl — Element 7 T20 — Chrome CDP driver
 *
 * Wraps a CDP-attached browser (typically an existing user Chrome launched
 * with --remote-debugging-port=9222) with the same surface as SafariDriver,
 * so the unified live_browser tool can dispatch by action name without
 * branching on which browser is frontmost.
 *
 * The driver itself stays browser-process-agnostic: it talks to a small
 * `ChromeBackend` shim (real backend in `chrome-backend.ts`, fakes in
 * tests). That keeps puppeteer out of the unit-test path and lets us stub
 * tab/active-page state cleanly.
 */
import { log } from "../../logger.js";

export interface PageLike {
  title(): Promise<string>;
  url(): string;
  goto(url: string): Promise<void>;
  click(selector: string): Promise<void>;
  type(selector: string, value: string): Promise<void>;
  evaluate<T>(fn: (...args: unknown[]) => T, ...args: unknown[]): Promise<T>;
  bringToFront(): Promise<void>;
  close(): Promise<void>;
  goBack(): Promise<void>;
  goForward(): Promise<void>;
}

export interface ChromeBackend {
  /** Snapshot of every open tab/page. */
  pages(): Promise<PageLike[]>;
  /** The page currently considered active (frontmost or last activated). */
  activePage(): Promise<PageLike>;
  /** Create a new tab. Optional initial URL navigates after creation. */
  newPage(url?: string): Promise<PageLike>;
  /**
   * Activate the tab at `index`. Backend is responsible for both calling
   * bringToFront on the page AND updating its own active-page bookkeeping
   * so subsequent `activePage()` calls return the right tab.
   * No-op when index is out of range.
   */
  activateTab(index: number): Promise<void>;
}

export interface BrowserTab {
  title: string;
  url: string;
}

export class ChromeDriver {
  constructor(private readonly backend: ChromeBackend) {}

  async listTabs(): Promise<BrowserTab[]> {
    log.tool.debug("chrome-driver.listTabs: entry");
    const pages = await this.backend.pages();
    const out: BrowserTab[] = [];
    for (const p of pages) {
      out.push({ title: await p.title(), url: p.url() });
    }
    log.tool.debug("chrome-driver.listTabs: exit", { tabCount: out.length });
    return out;
  }

  async activeTabUrl(): Promise<string | null> {
    log.tool.debug("chrome-driver.activeTabUrl: entry");
    const page = await this.backend.activePage();
    const u = page.url();
    const result = u && u.length > 0 ? u : null;
    log.tool.debug("chrome-driver.activeTabUrl: exit", { url: result });
    return result;
  }

  async activeTabText(): Promise<string> {
    log.tool.debug("chrome-driver.activeTabText: entry");
    const page = await this.backend.activePage();
    const text = await page.evaluate(() => document.body?.innerText ?? "");
    log.tool.debug("chrome-driver.activeTabText: exit", { textLen: text.length });
    return text;
  }

  async navigate(url: string): Promise<void> {
    log.tool.debug("chrome-driver.navigate: entry", { url });
    const page = await this.backend.activePage();
    await page.goto(url);
    log.tool.debug("chrome-driver.navigate: exit", { url });
  }

  async click(selector: string): Promise<void> {
    log.tool.debug("chrome-driver.click: entry", { selector });
    const page = await this.backend.activePage();
    await page.click(selector);
    log.tool.debug("chrome-driver.click: exit", { selector });
  }

  async fill(selector: string, value: string): Promise<void> {
    log.tool.debug("chrome-driver.fill: entry", { selector, valueLen: value.length });
    const page = await this.backend.activePage();
    await page.type(selector, value);
    log.tool.debug("chrome-driver.fill: exit", { selector });
  }

  async runJS<T = unknown>(fn: (...args: unknown[]) => T): Promise<T> {
    log.tool.debug("chrome-driver.runJS: entry");
    const page = await this.backend.activePage();
    const result = await page.evaluate(fn);
    log.tool.debug("chrome-driver.runJS: exit", { resultType: typeof result });
    return result;
  }

  async scroll(deltaPx: number): Promise<void> {
    log.tool.debug("chrome-driver.scroll: entry", { deltaPx });
    const page = await this.backend.activePage();
    const dy = Math.trunc(deltaPx);
    await page.evaluate(((d: number) => {
      window.scrollBy(0, d);
    }) as unknown as (...args: unknown[]) => void, dy);
    log.tool.debug("chrome-driver.scroll: exit", { deltaPx });
  }

  async newTab(url?: string): Promise<void> {
    log.tool.debug("chrome-driver.newTab: entry", { url });
    await this.backend.newPage(url);
    log.tool.debug("chrome-driver.newTab: exit", { url });
  }

  async closeTab(index: number): Promise<void> {
    log.tool.debug("chrome-driver.closeTab: entry", { index });
    const pages = await this.backend.pages();
    const target = pages[index];
    if (!target) {
      log.tool.debug("chrome-driver.closeTab: index out of range", { index, pageCount: pages.length });
      return;
    }
    await target.close();
    log.tool.debug("chrome-driver.closeTab: exit", { index });
  }

  async switchTab(index: number): Promise<void> {
    log.tool.debug("chrome-driver.switchTab: entry", { index });
    await this.backend.activateTab(index);
    log.tool.debug("chrome-driver.switchTab: exit", { index });
  }

  async back(): Promise<void> {
    log.tool.debug("chrome-driver.back: entry");
    const page = await this.backend.activePage();
    await page.goBack();
    log.tool.debug("chrome-driver.back: exit");
  }

  async forward(): Promise<void> {
    log.tool.debug("chrome-driver.forward: entry");
    const page = await this.backend.activePage();
    await page.goForward();
    log.tool.debug("chrome-driver.forward: exit");
  }
}
