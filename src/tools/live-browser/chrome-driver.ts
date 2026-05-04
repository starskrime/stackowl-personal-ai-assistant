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
    const pages = await this.backend.pages();
    const out: BrowserTab[] = [];
    for (const p of pages) {
      out.push({ title: await p.title(), url: p.url() });
    }
    return out;
  }

  async activeTabUrl(): Promise<string | null> {
    const page = await this.backend.activePage();
    const u = page.url();
    return u && u.length > 0 ? u : null;
  }

  async activeTabText(): Promise<string> {
    const page = await this.backend.activePage();
    return page.evaluate(() => document.body?.innerText ?? "");
  }

  async navigate(url: string): Promise<void> {
    const page = await this.backend.activePage();
    await page.goto(url);
  }

  async click(selector: string): Promise<void> {
    const page = await this.backend.activePage();
    await page.click(selector);
  }

  async fill(selector: string, value: string): Promise<void> {
    const page = await this.backend.activePage();
    await page.type(selector, value);
  }

  async runJS<T = unknown>(fn: (...args: unknown[]) => T): Promise<T> {
    const page = await this.backend.activePage();
    return page.evaluate(fn);
  }

  async scroll(deltaPx: number): Promise<void> {
    const page = await this.backend.activePage();
    const dy = Math.trunc(deltaPx);
    await page.evaluate(((d: number) => {
      window.scrollBy(0, d);
    }) as unknown as (...args: unknown[]) => void, dy);
  }

  async newTab(url?: string): Promise<void> {
    await this.backend.newPage(url);
  }

  async closeTab(index: number): Promise<void> {
    const pages = await this.backend.pages();
    const target = pages[index];
    if (!target) return;
    await target.close();
  }

  async switchTab(index: number): Promise<void> {
    await this.backend.activateTab(index);
  }

  async back(): Promise<void> {
    const page = await this.backend.activePage();
    await page.goBack();
  }

  async forward(): Promise<void> {
    const page = await this.backend.activePage();
    await page.goForward();
  }
}
