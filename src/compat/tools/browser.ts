/**
 * StackOwl — Unified Browser Tool
 *
 * Single tool for ALL browser automation — from simple page reads to full
 * Chrome DevTools Protocol (CDP) control. Replaces the old separate
 * browser + browser_cdp tools.
 *
 * Capabilities:
 *   - Page navigation, snapshots, element interaction (click/type/fill)
 *   - JavaScript execution in page context
 *   - Network request monitoring & interception
 *   - Console log capture
 *   - Cookie & storage management
 *   - Multi-tab control
 *   - PDF generation & screenshots
 *   - Performance profiling
 *   - Device emulation
 */

import puppeteer, {
  type Browser,
  type Page,
  type CDPSession,
  type HTTPRequest,
} from "puppeteer";
import type { ToolImplementation, ToolContext } from "../../tools/registry.js";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { log } from "../../logger.js";

// ─── Chrome auto-discovery ──────────────────────────────────────

function findChrome(): string | undefined {
  const candidates = [
    ...(() => {
      try {
        const base = join(
          process.env.HOME || "",
          ".cache",
          "puppeteer",
          "chrome",
        );
        if (!existsSync(base)) return [];
        const { readdirSync } = require("node:fs");
        return readdirSync(base)
          .sort()
          .reverse()
          .flatMap((ver: string) => [
            join(
              base,
              ver,
              "chrome-mac-arm64",
              "Google Chrome for Testing.app",
              "Contents",
              "MacOS",
              "Google Chrome for Testing",
            ),
            join(
              base,
              ver,
              "chrome-mac-x64",
              "Google Chrome for Testing.app",
              "Contents",
              "MacOS",
              "Google Chrome for Testing",
            ),
          ]);
      } catch {
        return [];
      }
    })(),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ];
  return candidates.find((p) => existsSync(p));
}

// ─── Types ──────────────────────────────────────────────────────

interface BrowserSession {
  browser: Browser;
  pages: Map<string, Page>;
  activePageId: string;
  cdpSession: CDPSession | null;
  networkLog: NetworkEntry[];
  consoleLog: ConsoleEntry[];
  interceptEnabled: boolean;
  interceptRules: InterceptRule[];
}

interface NetworkEntry {
  timestamp: number;
  method: string;
  url: string;
  status?: number;
  type?: string;
  size?: number;
}

interface ConsoleEntry {
  timestamp: number;
  type: string;
  text: string;
}

interface InterceptRule {
  pattern: string;
  action: "block" | "modify";
  headers?: Record<string, string>;
}

const MAX_LOG_ENTRIES = 200;
const NAV_TIMEOUT = 30_000;
const ELEMENT_SELECTOR =
  'a, button, input, select, textarea, [role="button"], [role="link"], [role="tab"]';

// ─── Tool ───────────────────────────────────────────────────────

export class BrowserTool implements ToolImplementation {
  private session: BrowserSession | null = null;
  private workspacePath: string;

  constructor(workspacePath: string = "./workspace") {
    this.workspacePath = workspacePath;
  }

  definition = {
    name: "browser",
    description: `Headless Chrome browser with full CDP (Chrome DevTools Protocol) control. Use when web_crawl fails (bot-blocked, login-gated, SPA) or when you need interactive web automation.

**Basic workflow:** start → navigate → snapshot → act → screenshot

**Core actions:**
- start: Launch browser (required first)
- stop: Close browser
- navigate url="...": Go to URL
- snapshot: Get page text + numbered interactive element refs
- act ref="5" act="click|type|fill|press|hover|select" [value="..."]: Interact with element
- screenshot [selector="..." | fullPage=true]: Capture screenshot
- status: Check browser state

**Advanced CDP actions:**
- execute script="...": Run JavaScript in page context
- query selector="..." [attribute="..."]: Query DOM by CSS selector
- network_log [filter="..."]: View captured requests
- network_clear: Clear network log
- intercept_add pattern="..." interceptAction="block|modify" [headers='{}']: Add request rule
- intercept_remove pattern="...": Remove rule
- intercept_list: List active rules
- console_log [filter="..."]: View console output
- console_clear: Clear console log
- cookies_get [domain="..."]: Get cookies
- cookies_set name="..." value="..." [domain="..."] [path="/"]: Set cookie
- cookies_clear [domain="..."]: Clear cookies
- storage_get key="..." [storageType="local|session"]: Get storage value
- storage_set key="..." value="..." [storageType="local|session"]: Set storage
- storage_clear [storageType="local|session"]: Clear storage
- tab_new [url="..."]: Open new tab
- tab_switch id="...": Switch tab
- tab_close [id="..."]: Close tab
- tab_list: List tabs
- pdf [path="..."]: Export page as PDF
- performance: Page performance metrics
- wait_for selector="..." [timeout=5000]: Wait for element
- wait_for_network [timeout=5000]: Wait for network idle
- emulate device="...": Emulate device (e.g. "iPhone 14")
- set_viewport width=1280 height=800

IMPORTANT: Always 'start' before other actions. Use snapshot refs for act commands. If bot-blocked, use computer_use tool instead.`,
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          description: "Action to perform",
        },
        url: { type: "string", description: "URL for navigate/tab_new" },
        ref: {
          type: "string",
          description: "Element ref number from snapshot (for act)",
        },
        act: {
          type: "string",
          description:
            "Interaction type: click, type, press, fill, hover, select",
        },
        value: {
          type: "string",
          description: "Text value for type/fill/press/cookies_set/storage_set",
        },
        script: { type: "string", description: "JavaScript code for execute" },
        selector: {
          type: "string",
          description: "CSS selector for query/wait_for/screenshot",
        },
        attribute: {
          type: "string",
          description: "Element attribute for query (default: textContent)",
        },
        filter: {
          type: "string",
          description: "Filter for network_log/console_log",
        },
        pattern: {
          type: "string",
          description: "URL pattern for intercept rules",
        },
        interceptAction: {
          type: "string",
          description: "Intercept action: block or modify",
        },
        headers: {
          type: "string",
          description: "JSON headers for intercept modify",
        },
        name: { type: "string", description: "Cookie name" },
        domain: { type: "string", description: "Cookie domain" },
        path: { type: "string", description: "Cookie path or PDF output path" },
        key: { type: "string", description: "Storage key" },
        storageType: {
          type: "string",
          description: "Storage type: local (default) or session",
        },
        id: { type: "string", description: "Tab ID" },
        timeout: { type: "number", description: "Timeout in ms" },
        fullPage: { type: "boolean", description: "Full page screenshot" },
        device: { type: "string", description: "Device name for emulate" },
        width: { type: "number", description: "Viewport width" },
        height: { type: "number", description: "Viewport height" },
      },
      required: ["action"],
    },
  };

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const action = args.action as string;

    try {
      switch (action) {
        // ── Core actions ──
        case "start":
          return await this.handleStart();
        case "stop":
        case "close":
          return await this.handleStop();
        case "status":
          return this.handleStatus();
        case "navigate":
        case "open":
          return await this.handleNavigate(args.url as string);
        case "snapshot":
          return await this.handleSnapshot();
        case "act":
          return await this.handleAct(args);
        case "screenshot":
          return await this.handleScreenshot(args, context);

        // ── CDP: JavaScript ──
        case "execute":
          return await this.handleExecute(args.script as string);
        case "query":
          return await this.handleQuery(
            args.selector as string,
            args.attribute as string,
          );

        // ── CDP: Network ──
        case "network_log":
          return this.handleNetworkLog(args.filter as string);
        case "network_clear":
          return this.handleNetworkClear();
        case "intercept_add":
          return await this.handleInterceptAdd(args);
        case "intercept_remove":
          return this.handleInterceptRemove(args.pattern as string);
        case "intercept_list":
          return this.handleInterceptList();

        // ── CDP: Console ──
        case "console_log":
          return this.handleConsoleLog(args.filter as string);
        case "console_clear":
          return this.handleConsoleClear();

        // ── CDP: Cookies ──
        case "cookies_get":
          return await this.handleCookiesGet(args.domain as string);
        case "cookies_set":
          return await this.handleCookiesSet(args);
        case "cookies_clear":
          return await this.handleCookiesClear(args.domain as string);

        // ── CDP: Storage ──
        case "storage_get":
          return await this.handleStorageGet(
            args.key as string,
            args.storageType as string,
          );
        case "storage_set":
          return await this.handleStorageSet(
            args.key as string,
            args.value as string,
            args.storageType as string,
          );
        case "storage_clear":
          return await this.handleStorageClear(args.storageType as string);

        // ── CDP: Tabs ──
        case "tab_new":
          return await this.handleTabNew(args.url as string);
        case "tab_switch":
          return await this.handleTabSwitch(args.id as string);
        case "tab_close":
          return await this.handleTabClose(args.id as string);
        case "tab_list":
          return this.handleTabList();

        // ── CDP: Export & Perf ──
        case "pdf":
          return await this.handlePDF(args.path as string, context);
        case "performance":
          return await this.handlePerformance();

        // ── CDP: Wait ──
        case "wait_for":
          return await this.handleWaitFor(
            args.selector as string,
            args.timeout as number,
          );
        case "wait_for_network":
          return await this.handleWaitForNetwork(args.timeout as number);

        // ── CDP: Emulation ──
        case "emulate":
          return await this.handleEmulate(args.device as string);
        case "set_viewport":
          return await this.handleSetViewport(args);

        default:
          return `ERROR: Unknown action "${action}".`;
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);

      // Auto-recover from stale profile lock
      if (
        action === "start" &&
        (msg.includes("already running") || msg.includes("SingletonLock"))
      ) {
        log.tool.warn(
          "[Browser] Stale lock detected — cleaning up and retrying",
        );
        await this.cleanProfileDir();
        return await this.handleStart();
      }

      return `ERROR: ${msg}`;
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // CORE ACTIONS (from original BrowserTool)
  // ═══════════════════════════════════════════════════════════════

  private handleStatus(): string {
    if (!this.session)
      return "Browser is not running. Use action=start to launch it.";
    const page = this.getActivePage();
    const tabCount = this.session.pages.size;
    return `Browser is running. Active page: ${page?.url() ?? "about:blank"} (${tabCount} tab${tabCount !== 1 ? "s" : ""})`;
  }

  private async handleStart(): Promise<string> {
    if (this.session) {
      const page = this.getActivePage();
      return `Browser already running. Current page: ${page?.url() ?? "about:blank"}`;
    }

    const userDataDir = join(
      this.workspacePath,
      ".browser-profiles",
      "default",
    );
    if (!existsSync(userDataDir)) mkdirSync(userDataDir, { recursive: true });
    await this.cleanProfileDir();

    const execPath = findChrome();
    log.tool.info(
      `[Browser] Launching Chrome${execPath ? ` from ${execPath}` : " (bundled)"}`,
    );

    const browser = await puppeteer.launch({
      headless: true,
      executablePath: execPath,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
      ],
      userDataDir,
      defaultViewport: { width: 1280, height: 800 },
    });

    const page = (await browser.pages())[0] || (await browser.newPage());
    await page.setUserAgent(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    );

    const pageId = `tab_0`;
    const pages = new Map<string, Page>();
    pages.set(pageId, page);

    this.session = {
      browser,
      pages,
      activePageId: pageId,
      cdpSession: null,
      networkLog: [],
      consoleLog: [],
      interceptEnabled: false,
      interceptRules: [],
    };

    await this.setupCDP(page);
    return "Browser started. Network & console logging active. Use action=navigate to go to a page.";
  }

  private async handleStop(): Promise<string> {
    if (!this.session) return "Browser was not running.";
    try {
      await this.session.browser.close();
    } catch {
      /* already closed */
    }
    this.session = null;
    return "Browser stopped.";
  }

  private async handleNavigate(url: string): Promise<string> {
    if (!url) return "ERROR: url parameter is required.";
    const page = this.requirePage();

    try {
      const parsed = new URL(url);
      if (!["http:", "https:"].includes(parsed.protocol)) {
        return "ERROR: Only http:// and https:// URLs are supported.";
      }
      url = parsed.toString();
    } catch {
      return `ERROR: Invalid URL: ${url}`;
    }

    try {
      await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: NAV_TIMEOUT,
      });
      await new Promise((r) => setTimeout(r, 1500));

      const title = await page.title();
      const bodyText = await page.evaluate(() =>
        (document.body?.innerText || "").slice(0, 500),
      );

      const botBlocked =
        title.includes("Security Checkpoint") ||
        title.includes("Just a moment") ||
        title.includes("Attention Required") ||
        bodyText.includes("Verify you are human") ||
        bodyText.includes("verifying your browser") ||
        bodyText.includes("Enable JavaScript and cookies") ||
        bodyText.includes("Checking your browser");

      if (botBlocked) {
        return (
          `BLOCKED: ${url} has bot protection ("${title}").\n` +
          `FALLBACK: Use the computer_use tool instead — it controls the REAL mouse and keyboard:\n` +
          `  1. computer_use(action:'open_url', text:'${url}')\n` +
          `  2. computer_use(action:'wait', amount:3000)\n` +
          `  3. computer_use(action:'analyze_screen') to read the page`
        );
      }

      return `Navigated to: ${url}\nTitle: ${title}\n\nUse action=snapshot to see content and interactive elements.`;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("timeout")) {
        return `Page took too long to load (>${NAV_TIMEOUT / 1000}s). Try action=snapshot to see what loaded.`;
      }
      return `Navigation failed: ${msg}`;
    }
  }

  private async handleSnapshot(): Promise<string> {
    const page = this.requirePage();

    try {
      await page.waitForFunction(() => document.readyState === "complete", {
        timeout: 5000,
      });
    } catch {
      /* streaming OK */
    }

    const url = page.url();
    const title = await page.title();

    // Bot detection on snapshot
    const quickCheck = await page.evaluate(() =>
      (document.body?.innerText || "").slice(0, 300),
    );
    if (
      title.includes("Security Checkpoint") ||
      title.includes("Just a moment") ||
      quickCheck.includes("verifying your browser") ||
      quickCheck.includes("Verify you are human")
    ) {
      return (
        `BLOCKED: Bot protection ("${title}").\n` +
        `FALLBACK: Use computer_use tool (open_url → wait → analyze_screen).`
      );
    }

    const pageText = await page.evaluate(() => {
      document
        .querySelectorAll("script, style, noscript, svg, iframe, nav")
        .forEach((el) => el.remove());
      const body = document.body;
      if (!body) return "(empty page)";
      return (body.innerText || body.textContent || "")
        .replace(/[\r\n]+/g, "\n")
        .replace(/[ \t]+/g, " ")
        .trim()
        .slice(0, 8000);
    });

    const elements = await page.evaluate((sel: string) => {
      const all = Array.from(document.querySelectorAll(sel));
      const results: Array<{
        ref: number;
        tag: string;
        type: string;
        text: string;
        placeholder: string;
        name: string;
      }> = [];
      for (let i = 0; i < all.length && results.length < 60; i++) {
        const el = all[i];
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        const tag = el.tagName.toLowerCase();
        const inputEl = el as HTMLInputElement;
        results.push({
          ref: i + 1,
          tag,
          type: inputEl.type || "",
          text:
            (el.textContent || "").trim().slice(0, 80) ||
            inputEl.placeholder ||
            inputEl.name ||
            el.getAttribute("aria-label") ||
            "(unlabeled)",
          placeholder: inputEl.placeholder || "",
          name: inputEl.name || el.getAttribute("aria-label") || "",
        });
      }
      return results;
    }, ELEMENT_SELECTOR);

    const lines: string[] = [`## Page: ${title}`, `URL: ${url}\n`];

    if (pageText) {
      const truncText =
        pageText.length > 4000
          ? pageText.slice(0, 4000) + "\n...[truncated]"
          : pageText;
      lines.push(`### Content\n${truncText}\n`);
    }

    if (elements.length > 0) {
      lines.push(
        `### Interactive Elements\nUse ref numbers with action=act:\n`,
      );
      for (const el of elements) {
        const label =
          el.tag === "input" || el.tag === "textarea"
            ? `[${el.tag} type=${el.type}] "${el.placeholder || el.name || el.text}"`
            : el.tag === "a"
              ? `[link] "${el.text}"`
              : el.tag === "button"
                ? `[button] "${el.text}"`
                : el.tag === "select"
                  ? `[dropdown] "${el.name || el.text}"`
                  : `[${el.tag}] "${el.text}"`;
        lines.push(`  [ref:${el.ref}] ${label}`);
      }
    } else {
      lines.push("(No interactive elements found)");
    }

    return lines.join("\n");
  }

  private async handleAct(args: Record<string, unknown>): Promise<string> {
    const page = this.requirePage();
    const actType = (args.act as string) || "click";
    const ref = args.ref as string;
    const value = (args.value as string) || "";

    if (!ref)
      return "ERROR: ref parameter is required. Use snapshot to get element refs.";
    const refNum = parseInt(String(ref).replace(/\D/g, ""), 10);
    if (isNaN(refNum) || refNum < 1) return `ERROR: Invalid ref "${ref}".`;

    switch (actType) {
      case "click": {
        const navP = page
          .waitForNavigation({ waitUntil: "domcontentloaded", timeout: 5000 })
          .catch(() => null);
        const found = await page.evaluate(
          (sel: string, idx: number) => {
            const el = document.querySelectorAll(sel)[idx - 1] as
              | HTMLElement
              | undefined;
            if (!el) return false;
            el.click();
            return true;
          },
          ELEMENT_SELECTOR,
          refNum,
        );
        if (!found)
          return `ERROR: Element ref ${refNum} not found. Run snapshot again.`;
        await navP;
        await new Promise((r) => setTimeout(r, 500));
        return `Clicked [ref:${refNum}]. Page: ${page.url()}\nUse snapshot to see updated content.`;
      }

      case "type": {
        if (!value) return "ERROR: value required for type.";
        const focused = await page.evaluate(
          (sel: string, idx: number) => {
            const el = document.querySelectorAll(sel)[idx - 1] as
              | HTMLElement
              | undefined;
            if (!el) return false;
            el.focus();
            if (
              el instanceof HTMLInputElement ||
              el instanceof HTMLTextAreaElement
            )
              el.select();
            return true;
          },
          ELEMENT_SELECTOR,
          refNum,
        );
        if (!focused) return `ERROR: Element ref ${refNum} not found.`;
        await page.keyboard.type(value, { delay: 20 });
        return `Typed "${value}" into [ref:${refNum}].`;
      }

      case "fill": {
        if (!value) return "ERROR: value required for fill.";
        const filled = await page.evaluate(
          (sel: string, idx: number, val: string) => {
            const el = document.querySelectorAll(sel)[idx - 1] as
              | HTMLInputElement
              | undefined;
            if (!el) return false;
            el.value = val;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            return true;
          },
          ELEMENT_SELECTOR,
          refNum,
          value,
        );
        if (!filled) return `ERROR: Element ref ${refNum} not found.`;
        return `Filled [ref:${refNum}] with "${value}".`;
      }

      case "press": {
        const key = value || "Enter";
        const navP =
          key === "Enter"
            ? page
                .waitForNavigation({
                  waitUntil: "domcontentloaded",
                  timeout: 10000,
                })
                .catch(() => null)
            : null;
        await page.keyboard.press(key as any);
        if (navP) await navP;
        await new Promise((r) => setTimeout(r, 1000));
        return `Pressed ${key}. Page: ${page.url()}`;
      }

      case "hover": {
        const ok = await page.evaluate(
          (sel: string, idx: number) => {
            const el = document.querySelectorAll(sel)[idx - 1] as
              | HTMLElement
              | undefined;
            if (!el) return false;
            el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
            return true;
          },
          ELEMENT_SELECTOR,
          refNum,
        );
        if (!ok) return `ERROR: Element ref ${refNum} not found.`;
        return `Hovered over [ref:${refNum}].`;
      }

      case "select": {
        if (!value) return "ERROR: value required for select.";
        const ok = await page.evaluate(
          (sel: string, idx: number, val: string) => {
            const el = document.querySelectorAll(sel)[idx - 1] as
              | HTMLSelectElement
              | undefined;
            if (!el) return false;
            el.value = val;
            el.dispatchEvent(new Event("change", { bubbles: true }));
            return true;
          },
          ELEMENT_SELECTOR,
          refNum,
          value,
        );
        if (!ok) return `ERROR: Element ref ${refNum} not found.`;
        return `Selected "${value}" in [ref:${refNum}].`;
      }

      default:
        return `ERROR: Unknown act type "${actType}". Use: click, type, fill, press, hover, select`;
    }
  }

  private async handleScreenshot(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const page = this.requirePage();
    const dir = join(context?.cwd || this.workspacePath, "screenshots");
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });

    const filename = `browser_${Date.now()}.png`;
    const filepath = join(dir, filename);
    const selector = args.selector as string;

    if (selector) {
      const element = await page.$(selector);
      if (!element) return `ERROR: Element "${selector}" not found.`;
      await element.screenshot({ path: filepath });
    } else {
      await page.screenshot({
        path: filepath,
        fullPage: args.fullPage === true,
      });
    }

    return `Screenshot saved: ${filepath}\nUse send_file to deliver it to the user.`;
  }

  // ═══════════════════════════════════════════════════════════════
  // CDP: JAVASCRIPT EXECUTION
  // ═══════════════════════════════════════════════════════════════

  private async handleExecute(script: string): Promise<string> {
    if (!script) return "ERROR: script is required.";
    const page = this.requirePage();

    const result = await page.evaluate((code: string) => {
      try {
        const fn = new Function(code);
        const res = fn();
        if (res instanceof Promise)
          return "ERROR: Async not supported. Wrap in page.evaluate.";
        if (res === undefined) return "(undefined)";
        if (res === null) return "(null)";
        if (typeof res === "object") return JSON.stringify(res, null, 2);
        return String(res);
      } catch (e: any) {
        return `ERROR: ${e.message}`;
      }
    }, script);

    const s = String(result);
    return s.length > 5000 ? s.slice(0, 5000) + "\n...[truncated]" : s;
  }

  private async handleQuery(
    selector: string,
    attribute?: string,
  ): Promise<string> {
    if (!selector) return "ERROR: selector is required.";
    const page = this.requirePage();
    const attr = attribute || "textContent";

    const results = await page.evaluate(
      (sel: string, a: string) => {
        const out: Array<{ i: number; tag: string; val: string }> = [];
        document.querySelectorAll(sel).forEach((el, i) => {
          if (i >= 50) return;
          let val: string;
          if (a === "textContent")
            val = (el.textContent || "").trim().slice(0, 200);
          else if (a === "innerHTML") val = (el.innerHTML || "").slice(0, 500);
          else if (a === "outerHTML") val = (el.outerHTML || "").slice(0, 500);
          else val = el.getAttribute(a) || "";
          out.push({ i, tag: el.tagName.toLowerCase(), val });
        });
        return out;
      },
      selector,
      attr,
    );

    if (results.length === 0) return `No elements matching "${selector}".`;
    return (
      `Found ${results.length} element(s):\n` +
      results.map((r) => `[${r.i}] <${r.tag}> ${r.val}`).join("\n")
    );
  }

  // ═══════════════════════════════════════════════════════════════
  // CDP: NETWORK
  // ═══════════════════════════════════════════════════════════════

  private handleNetworkLog(filter?: string): string {
    if (!this.session) return this.notStartedMsg();
    let entries = this.session.networkLog;
    if (filter) {
      const f = filter.toLowerCase();
      entries = entries.filter(
        (e) =>
          e.url.toLowerCase().includes(f) ||
          (e.type || "").toLowerCase().includes(f),
      );
    }
    if (entries.length === 0)
      return (
        "No network requests" + (filter ? ` matching "${filter}"` : "") + "."
      );
    const recent = entries.slice(-30);
    return (
      `Network (${entries.length} total, last ${recent.length}):\n` +
      recent
        .map(
          (e) =>
            `${e.method} [${e.status ?? "?"}] ${e.url.slice(0, 120)} (${e.type || "?"})`,
        )
        .join("\n")
    );
  }

  private handleNetworkClear(): string {
    if (!this.session) return this.notStartedMsg();
    this.session.networkLog = [];
    return "Network log cleared.";
  }

  private async handleInterceptAdd(
    args: Record<string, unknown>,
  ): Promise<string> {
    const page = this.requirePage();
    const pattern = args.pattern as string;
    if (!pattern) return "ERROR: pattern is required.";
    const iAction =
      (args.interceptAction as string) === "modify" ? "modify" : "block";

    let headers: Record<string, string> | undefined;
    if (iAction === "modify" && args.headers) {
      try {
        headers = JSON.parse(args.headers as string);
      } catch {
        return "ERROR: headers must be valid JSON.";
      }
    }

    this.session!.interceptRules.push({ pattern, action: iAction, headers });

    if (!this.session!.interceptEnabled) {
      await page.setRequestInterception(true);
      page.on("request", (request: HTTPRequest) => {
        if (!this.session) {
          request.continue();
          return;
        }
        for (const r of this.session.interceptRules) {
          if (this.matchGlob(request.url(), r.pattern)) {
            if (r.action === "block") {
              request.abort("blockedbyclient");
              return;
            }
            if (r.action === "modify" && r.headers) {
              request.continue({
                headers: { ...request.headers(), ...r.headers },
              });
              return;
            }
          }
        }
        request.continue();
      });
      this.session!.interceptEnabled = true;
    }

    return (
      `Intercept: ${iAction} "${pattern}"` +
      (headers ? ` headers: ${JSON.stringify(headers)}` : "")
    );
  }

  private handleInterceptRemove(pattern: string): string {
    if (!this.session) return this.notStartedMsg();
    if (!pattern) return "ERROR: pattern is required.";
    const before = this.session.interceptRules.length;
    this.session.interceptRules = this.session.interceptRules.filter(
      (r) => r.pattern !== pattern,
    );
    const removed = before - this.session.interceptRules.length;
    return removed > 0
      ? `Removed ${removed} rule(s) for "${pattern}".`
      : `No rule for "${pattern}".`;
  }

  private handleInterceptList(): string {
    if (!this.session) return this.notStartedMsg();
    if (this.session.interceptRules.length === 0) return "No intercept rules.";
    return (
      "Intercept rules:\n" +
      this.session.interceptRules
        .map(
          (r) =>
            `  ${r.action.toUpperCase()} "${r.pattern}"` +
            (r.headers ? ` ${JSON.stringify(r.headers)}` : ""),
        )
        .join("\n")
    );
  }

  // ═══════════════════════════════════════════════════════════════
  // CDP: CONSOLE
  // ═══════════════════════════════════════════════════════════════

  private handleConsoleLog(filter?: string): string {
    if (!this.session) return this.notStartedMsg();
    let entries = this.session.consoleLog;
    if (filter) {
      const f = filter.toLowerCase();
      entries = entries.filter(
        (e) => e.text.toLowerCase().includes(f) || e.type.includes(f),
      );
    }
    if (entries.length === 0)
      return (
        "No console output" + (filter ? ` matching "${filter}"` : "") + "."
      );
    const recent = entries.slice(-30);
    return (
      `Console (${entries.length} total, last ${recent.length}):\n` +
      recent.map((e) => `[${e.type}] ${e.text.slice(0, 200)}`).join("\n")
    );
  }

  private handleConsoleClear(): string {
    if (!this.session) return this.notStartedMsg();
    this.session.consoleLog = [];
    return "Console log cleared.";
  }

  // ═══════════════════════════════════════════════════════════════
  // CDP: COOKIES
  // ═══════════════════════════════════════════════════════════════

  private async handleCookiesGet(domain?: string): Promise<string> {
    const page = this.requirePage();
    let cookies = await page.cookies();
    if (domain) cookies = cookies.filter((c) => c.domain.includes(domain));
    if (cookies.length === 0)
      return "No cookies" + (domain ? ` for "${domain}"` : "") + ".";
    return (
      `Cookies (${cookies.length}):\n` +
      cookies
        .slice(0, 30)
        .map(
          (c) =>
            `  ${c.name}=${c.value.slice(0, 50)}${c.value.length > 50 ? "..." : ""} (${c.domain})`,
        )
        .join("\n")
    );
  }

  private async handleCookiesSet(
    args: Record<string, unknown>,
  ): Promise<string> {
    const page = this.requirePage();
    const name = args.name as string;
    const value = args.value as string;
    if (!name || !value) return "ERROR: name and value required.";
    await page.setCookie({
      name,
      value,
      url: page.url(),
      domain: (args.domain as string) || undefined,
      path: (args.path as string) || "/",
    });
    return `Cookie "${name}" set.`;
  }

  private async handleCookiesClear(domain?: string): Promise<string> {
    const page = this.requirePage();
    const cookies = await page.cookies();
    const toDelete = domain
      ? cookies.filter((c) => c.domain.includes(domain))
      : cookies;
    for (const c of toDelete) await page.deleteCookie(c);
    return `Cleared ${toDelete.length} cookie(s).`;
  }

  // ═══════════════════════════════════════════════════════════════
  // CDP: STORAGE
  // ═══════════════════════════════════════════════════════════════

  private async handleStorageGet(key: string, type?: string): Promise<string> {
    if (!key) return "ERROR: key is required.";
    const page = this.requirePage();
    const st = type === "session" ? "sessionStorage" : "localStorage";
    const val = await page.evaluate(
      (k: string, s: string) => (window as any)[s]?.getItem(k),
      key,
      st,
    );
    if (val === null || val === undefined) return `${st}["${key}"] not set.`;
    return `${st}["${key}"] = ${String(val).slice(0, 2000)}`;
  }

  private async handleStorageSet(
    key: string,
    value: string,
    type?: string,
  ): Promise<string> {
    if (!key || value === undefined) return "ERROR: key and value required.";
    const page = this.requirePage();
    const st = type === "session" ? "sessionStorage" : "localStorage";
    await page.evaluate(
      (k: string, v: string, s: string) => (window as any)[s]?.setItem(k, v),
      key,
      value,
      st,
    );
    return `${st}["${key}"] set.`;
  }

  private async handleStorageClear(type?: string): Promise<string> {
    const page = this.requirePage();
    const st = type === "session" ? "sessionStorage" : "localStorage";
    await page.evaluate((s: string) => (window as any)[s]?.clear(), st);
    return `${st} cleared.`;
  }

  // ═══════════════════════════════════════════════════════════════
  // CDP: TABS
  // ═══════════════════════════════════════════════════════════════

  private async handleTabNew(url?: string): Promise<string> {
    if (!this.session) return this.notStartedMsg();
    const page = await this.session.browser.newPage();
    const pageId = `tab_${this.session.pages.size}`;
    this.session.pages.set(pageId, page);
    this.session.activePageId = pageId;
    await this.setupCDP(page);
    if (url)
      await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: NAV_TIMEOUT,
      });
    return `New tab: ${pageId}` + (url ? ` → ${url}` : "");
  }

  private async handleTabSwitch(id: string): Promise<string> {
    if (!this.session) return this.notStartedMsg();
    if (!id) return "ERROR: id required.";
    if (!this.session.pages.has(id)) return `ERROR: Tab "${id}" not found.`;
    this.session.activePageId = id;
    await this.session.pages.get(id)!.bringToFront();
    return `Switched to ${id}: ${this.session.pages.get(id)!.url()}`;
  }

  private async handleTabClose(id?: string): Promise<string> {
    if (!this.session) return this.notStartedMsg();
    const targetId = id || this.session.activePageId;
    const page = this.session.pages.get(targetId);
    if (!page) return `ERROR: Tab "${targetId}" not found.`;
    await page.close();
    this.session.pages.delete(targetId);
    if (targetId === this.session.activePageId) {
      const remaining = [...this.session.pages.keys()];
      if (remaining.length > 0) this.session.activePageId = remaining[0];
    }
    return `Closed ${targetId}. ${this.session.pages.size} tab(s) left.`;
  }

  private handleTabList(): string {
    if (!this.session) return this.notStartedMsg();
    return (
      `Tabs (${this.session.pages.size}):\n` +
      [...this.session.pages.entries()]
        .map(
          ([id, p]) =>
            `  ${id}${id === this.session!.activePageId ? " (active)" : ""}: ${p.url()}`,
        )
        .join("\n")
    );
  }

  // ═══════════════════════════════════════════════════════════════
  // CDP: PDF & PERFORMANCE
  // ═══════════════════════════════════════════════════════════════

  private async handlePDF(
    outputPath?: string,
    context?: ToolContext,
  ): Promise<string> {
    const page = this.requirePage();
    const dir = join(context?.cwd || this.workspacePath, "exports");
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    const filename = outputPath || `page_${Date.now()}.pdf`;
    const fullPath = filename.startsWith("/") ? filename : join(dir, filename);
    await page.pdf({
      path: fullPath,
      format: "A4",
      printBackground: true,
      margin: { top: "1cm", right: "1cm", bottom: "1cm", left: "1cm" },
    });
    return `PDF saved: ${fullPath}`;
  }

  private async handlePerformance(): Promise<string> {
    const page = this.requirePage();
    const metrics = await page.metrics();
    const perf = await page.evaluate(() => {
      const t = performance.getEntriesByType("navigation")[0] as
        | PerformanceNavigationTiming
        | undefined;
      if (!t) return null;
      return {
        dns: Math.round(t.domainLookupEnd - t.domainLookupStart),
        tcp: Math.round(t.connectEnd - t.connectStart),
        ttfb: Math.round(t.responseStart - t.requestStart),
        domLoaded: Math.round(t.domContentLoadedEventEnd - t.startTime),
        fullLoad: Math.round(t.loadEventEnd - t.startTime),
        domCount: document.querySelectorAll("*").length,
      };
    });

    const lines: string[] = ["**Performance:**"];
    if (perf) {
      lines.push(
        `DNS: ${perf.dns}ms | TCP: ${perf.tcp}ms | TTFB: ${perf.ttfb}ms`,
      );
      lines.push(
        `DOM loaded: ${perf.domLoaded}ms | Full: ${perf.fullLoad}ms | Elements: ${perf.domCount}`,
      );
    }
    lines.push(
      `JS heap: ${((metrics.JSHeapUsedSize || 0) / 1024 / 1024).toFixed(1)}MB / ${((metrics.JSHeapTotalSize || 0) / 1024 / 1024).toFixed(1)}MB`,
    );
    lines.push(
      `Nodes: ${metrics.Nodes || 0} | Listeners: ${metrics.JSEventListeners || 0}`,
    );

    const net = this.session?.networkLog || [];
    if (net.length > 0) {
      const types = new Map<string, number>();
      for (const e of net)
        types.set(e.type || "other", (types.get(e.type || "other") || 0) + 1);
      lines.push(
        `Network: ${net.length} requests — ${[...types.entries()].map(([t, c]) => `${t}(${c})`).join(", ")}`,
      );
    }
    return lines.join("\n");
  }

  // ═══════════════════════════════════════════════════════════════
  // CDP: WAIT
  // ═══════════════════════════════════════════════════════════════

  private async handleWaitFor(
    selector: string,
    timeout?: number,
  ): Promise<string> {
    if (!selector) return "ERROR: selector required.";
    const page = this.requirePage();
    try {
      await page.waitForSelector(selector, { timeout: timeout || 5000 });
      return `Element "${selector}" found.`;
    } catch {
      return `Timeout: "${selector}" not found within ${timeout || 5000}ms.`;
    }
  }

  private async handleWaitForNetwork(timeout?: number): Promise<string> {
    const page = this.requirePage();
    try {
      await page.waitForNetworkIdle({ timeout: timeout || 5000 });
      return "Network idle.";
    } catch {
      return `Timeout: network not idle within ${timeout || 5000}ms.`;
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // CDP: EMULATION
  // ═══════════════════════════════════════════════════════════════

  private async handleEmulate(device: string): Promise<string> {
    if (!device) return "ERROR: device required.";
    const page = this.requirePage();
    const d = (puppeteer.KnownDevices as any)[device];
    if (!d) {
      const names = Object.keys(puppeteer.KnownDevices).slice(0, 15).join(", ");
      return `ERROR: Unknown device. Available: ${names}...`;
    }
    await page.emulate(d);
    return `Emulating: ${device} (${d.viewport.width}x${d.viewport.height})`;
  }

  private async handleSetViewport(
    args: Record<string, unknown>,
  ): Promise<string> {
    const page = this.requirePage();
    const w = (args.width as number) || 1280;
    const h = (args.height as number) || 800;
    await page.setViewport({
      width: w,
      height: h,
      deviceScaleFactor: (args.deviceScaleFactor as number) || 1,
    });
    return `Viewport: ${w}x${h}`;
  }

  // ═══════════════════════════════════════════════════════════════
  // HELPERS
  // ═══════════════════════════════════════════════════════════════

  private async setupCDP(page: Page): Promise<void> {
    if (!this.session) return;
    const client = await page.createCDPSession();
    this.session.cdpSession = client;
    await client.send("Network.enable");

    page.on("response", async (response) => {
      if (!this.session) return;
      const req = response.request();
      this.session.networkLog.push({
        timestamp: Date.now(),
        method: req.method(),
        url: req.url(),
        status: response.status(),
        type: req.resourceType(),
      });
      if (this.session.networkLog.length > MAX_LOG_ENTRIES)
        this.session.networkLog.shift();
    });

    page.on("console", (msg) => {
      if (!this.session) return;
      this.session.consoleLog.push({
        timestamp: Date.now(),
        type: msg.type(),
        text: msg.text(),
      });
      if (this.session.consoleLog.length > MAX_LOG_ENTRIES)
        this.session.consoleLog.shift();
    });
  }

  private getActivePage(): Page | undefined {
    return this.session?.pages.get(this.session.activePageId);
  }

  private requirePage(): Page {
    if (!this.session)
      throw new Error("Browser not started. Use action=start first.");
    const page = this.session.pages.get(this.session.activePageId);
    if (!page) throw new Error("No active tab. Use tab_new to create one.");
    return page;
  }

  private notStartedMsg(): string {
    return "ERROR: Browser not started. Use action=start first.";
  }

  private async cleanProfileDir(): Promise<void> {
    const dir = join(this.workspacePath, ".browser-profiles", "default");
    for (const lock of [
      "SingletonLock",
      "SingletonSocket",
      "SingletonCookie",
    ]) {
      const p = join(dir, lock);
      try {
        if (existsSync(p)) rmSync(p, { force: true });
      } catch {
        /* non-fatal */
      }
    }
  }

  private matchGlob(url: string, pattern: string): boolean {
    const regex = new RegExp(
      "^" + pattern.replace(/\*/g, ".*").replace(/\?/g, ".") + "$",
      "i",
    );
    return regex.test(url);
  }

  async cleanup(): Promise<void> {
    await this.handleStop();
  }
}
