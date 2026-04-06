/**
 * StackOwl — Browser CDP Bridge
 *
 * Connects to Chrome/Chromium via Chrome DevTools Protocol (CDP),
 * providing reliable DOM-based automation as an alternative to the
 * AX tree / screenshot approach for browser tasks.
 *
 * Why CDP beats AX tree for web content:
 *   AX tree:   limited web exposure, ~1-2s per read, no selector access
 *   CDP:       full DOM, stable selectors, ~50-100ms per action, 10x more reliable
 *
 * Two connection modes:
 *   connect(port)  — attach to an existing Chrome launched with:
 *                    --remote-debugging-port=9222
 *   launch()       — spin up a fresh Puppeteer-managed Chromium (no config needed)
 *
 * Singleton — one bridge shared across all tool calls in a session.
 *
 * Uses Puppeteer (already a project dependency, no new install).
 */

import puppeteer from "puppeteer";
import type { Browser, Page } from "puppeteer";
import { existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

// ─── Types ───────────────────────────────────────────────────────────────────

export interface PageSnapshot {
  title: string;
  url: string;
  /** Structured text representation — equivalent to analyze_screen for web pages */
  content: string;
}

// ─── BrowserBridge ───────────────────────────────────────────────────────────

export class BrowserBridge {
  private static instance: BrowserBridge | null = null;

  private browser: Browser | null = null;
  private page: Page | null = null;

  // ─── Singleton ───────────────────────────────────────────────

  static getInstance(): BrowserBridge {
    if (!BrowserBridge.instance) {
      BrowserBridge.instance = new BrowserBridge();
    }
    return BrowserBridge.instance;
  }

  isConnected(): boolean {
    return this.browser !== null && this.page !== null;
  }

  // ─── Connection management ────────────────────────────────────

  /**
   * Attach to an already-running Chrome.
   * Launch Chrome with: google-chrome --remote-debugging-port=9222
   * or: open -a "Google Chrome" --args --remote-debugging-port=9222
   */
  async connect(port = 9222): Promise<void> {
    await this.teardown();
    this.browser = await puppeteer.connect({
      browserURL: `http://localhost:${port}`,
      defaultViewport: null,
    });
    const pages = await this.browser.pages();
    this.page = pages[0] ?? (await this.browser.newPage());
  }

  /**
   * Launch a fresh Chromium instance managed by Puppeteer.
   * headless=false opens a visible window (default — user can see what's happening).
   */
  async launch(url?: string, headless = false): Promise<void> {
    await this.teardown();
    this.browser = await puppeteer.launch({
      headless,
      defaultViewport: null,
      args: [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        // Reduce bot-detection signals
        "--disable-blink-features=AutomationControlled",
      ],
      ignoreDefaultArgs: ["--enable-automation"],
    });

    const pages = await this.browser.pages();
    this.page = pages[0] ?? (await this.browser.newPage());

    if (url) {
      await this.page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
    }
  }

  async disconnect(): Promise<void> {
    await this.teardown();
    BrowserBridge.instance = null;
  }

  private async teardown(): Promise<void> {
    if (!this.browser) return;
    try {
      // connect() path — detach without closing the external browser
      await (this.browser as Browser).disconnect();
    } catch {
      // launch() path — close our managed instance
      try { await this.browser.close(); } catch { /* ignore */ }
    }
    this.browser = null;
    this.page = null;
  }

  // ─── Page helpers ─────────────────────────────────────────────

  private async getPage(): Promise<Page> {
    if (!this.browser || !this.page) {
      throw new Error(
        "Not connected to a browser.\n" +
        "Use browser_connect (existing Chrome) or browser_launch (new window) first.",
      );
    }
    // If our tracked page was closed, grab the first available one
    const pages = await this.browser.pages();
    if (pages.length === 0) throw new Error("No open pages in browser.");
    if (!pages.includes(this.page)) this.page = pages[0];
    return this.page;
  }

  // ─── Navigation ───────────────────────────────────────────────

  async navigate(url: string): Promise<{ title: string; url: string }> {
    const page = await this.getPage();
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
    return { title: await page.title(), url: page.url() };
  }

  // ─── DOM snapshot ─────────────────────────────────────────────

  /**
   * Returns a structured text description of the current page.
   * Equivalent to analyze_screen for desktop apps, but for web content.
   *
   * Output format mirrors analyze_screen:
   *   [ref:1] input "Search" (sel: #q)
   *   [ref:2] button "Google Search" (sel: button[name=btnK])
   *   [ref:3] link "Wikipedia" → https://en.wikipedia.org
   *   --- CONTENT ---
   *   Lorem ipsum ...
   *
   * Use the sel: value with browser_click/browser_type for reliable element targeting.
   */
  async getPageText(): Promise<string> {
    const page = await this.getPage();

    const snapshot = await page.evaluate((): string => {
      const lines: string[] = [];
      lines.push(`PAGE: ${document.title}`);
      lines.push(`URL: ${location.href}`);
      lines.push("");

      let refN = 0;

      // ── Inputs / Textareas / Selects ─────────────────────────
      document
        .querySelectorAll<HTMLElement>(
          "input:not([type=hidden]):not([type=submit]):not([type=button])," +
          "input:not([type]):not([type=hidden])," +
          "textarea,select",
        )
        .forEach((el) => {
          const inp = el as HTMLInputElement;
          const label =
            inp.placeholder ||
            inp.getAttribute("aria-label") ||
            inp.getAttribute("name") ||
            inp.id ||
            "";
          const value = inp.value || "";
          const type = inp.type || "text";
          const sel = inp.id
            ? `#${inp.id}`
            : inp.name
              ? `[name="${inp.name}"]`
              : el.tagName.toLowerCase();
          refN++;
          lines.push(
            `[ref:${refN}] input type=${type} label="${label}"` +
            (value ? ` value="${value.slice(0, 100)}"` : "") +
            ` (sel: ${sel})`,
          );
        });

      // ── Buttons ───────────────────────────────────────────────
      document
        .querySelectorAll<HTMLElement>(
          'button,[role="button"],input[type="submit"],input[type="button"]',
        )
        .forEach((el) => {
          const text = (
            el.textContent ||
            el.getAttribute("aria-label") ||
            (el as HTMLInputElement).value ||
            ""
          ).trim();
          if (!text || text.length > 100) return;
          const sel = el.id ? `#${el.id}` : el.tagName.toLowerCase();
          refN++;
          lines.push(`[ref:${refN}] button "${text}" (sel: ${sel})`);
        });

      // ── Links (capped at 40 to avoid token overflow) ──────────
      let linkCount = 0;
      document.querySelectorAll<HTMLAnchorElement>("a[href]").forEach((el) => {
        if (linkCount >= 40) return;
        const text = (el.textContent || "").trim();
        if (!text || text.length > 120 || text.length < 2) return;
        const href = el.href;
        if (href.startsWith("javascript:") || href === location.href + "#") return;
        refN++;
        linkCount++;
        lines.push(`[ref:${refN}] link "${text}" → ${href}`);
      });

      // ── Main page content ─────────────────────────────────────
      const main =
        document.querySelector("main,[role=main],article,#content,.content") ||
        document.body;
      if (main) {
        const bodyText = (main.textContent || "")
          .replace(/\s+/g, " ")
          .trim()
          .slice(0, 3000);
        if (bodyText) {
          lines.push("");
          lines.push("--- CONTENT ---");
          lines.push(bodyText);
        }
      }

      lines.push("");
      lines.push(`Total refs: ${refN}`);
      lines.push(
        'To click: browser_click(selector:"<sel>") or browser_click(text:"visible text")',
      );
      lines.push('To fill:  browser_type(selector:"<sel>", text:"value")');

      return lines.join("\n");
    });

    return snapshot;
  }

  // ─── Interactions ─────────────────────────────────────────────

  /**
   * Click an element.
   * Tries CSS selector first, then falls back to visible text matching.
   */
  async click(selector?: string, text?: string): Promise<void> {
    const page = await this.getPage();

    if (selector) {
      try {
        await page.waitForSelector(selector, { timeout: 5_000 });
        await page.click(selector);
        return;
      } catch {
        // Fall through to text-based match
      }
    }

    if (text) {
      const clicked = await page.evaluate((t: string) => {
        const candidates = Array.from(
          document.querySelectorAll<HTMLElement>(
            'a,button,[role="button"],input[type="submit"],input[type="button"]',
          ),
        );
        const target = candidates.find((el) =>
          (
            el.textContent ||
            el.getAttribute("aria-label") ||
            (el as HTMLInputElement).value ||
            ""
          )
            .toLowerCase()
            .includes(t.toLowerCase()),
        );
        if (target) {
          target.click();
          return true;
        }
        return false;
      }, text);

      if (!clicked) throw new Error(`No element found with text: "${text}"`);
      return;
    }

    throw new Error("browser_click requires selector or text parameter.");
  }

  /**
   * Fill a form input.
   * Clears existing value, then types the new text.
   */
  async fill(selector: string, text: string): Promise<void> {
    const page = await this.getPage();
    await page.waitForSelector(selector, { timeout: 5_000 });
    // Triple-click to select all existing text before typing
    await page.click(selector, { clickCount: 3 });
    await page.type(selector, text, { delay: 0 });
  }

  /**
   * Evaluate a JavaScript expression in the page context.
   * Returns the result as a JSON-serializable value.
   */
  async evaluate(script: string): Promise<unknown> {
    const page = await this.getPage();
    return await page.evaluate(script);
  }

  /**
   * Save a screenshot of the browser viewport.
   */
  async screenshot(outputPath: string): Promise<void> {
    const dir = dirname(outputPath);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    const page = await this.getPage();
    await page.screenshot({ path: outputPath });
  }

  /**
   * Wait for an element matching selector to appear in the DOM.
   */
  async waitForSelector(selector: string, timeoutMs = 10_000): Promise<boolean> {
    const page = await this.getPage();
    try {
      await page.waitForSelector(selector, { timeout: timeoutMs });
      return true;
    } catch {
      return false;
    }
  }
}
