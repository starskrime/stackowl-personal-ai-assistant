/**
 * StackOwl — Browser Tool
 *
 * Headless Chrome automation via Puppeteer. Handles interactive sites,
 * bot-blocked pages, SPAs, and form submission that web_crawl can't do.
 */

import puppeteer, { type Browser, type Page } from "puppeteer";
import type { ToolImplementation, ToolContext } from "../../tools/registry.js";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { log } from "../../logger.js";

// ─── Chrome auto-discovery ──────────────────────────────────────

function findChrome(): string | undefined {
  const candidates = [
    // Puppeteer's managed Chrome
    ...((() => {
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
    })()),
    // System Chrome
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ];
  return candidates.find((p) => existsSync(p));
}

// ─── Types ──────────────────────────────────────────────────────

interface BrowserProfile {
  id: string;
  browser: Browser;
  page: Page;
}

const DEFAULT_PROFILE = "default";
const NAV_TIMEOUT = 30_000;

// ─── Tool ───────────────────────────────────────────────────────

export class BrowserTool implements ToolImplementation {
  private profiles: Map<string, BrowserProfile> = new Map();
  private workspacePath: string;

  constructor(workspacePath: string = "./workspace") {
    this.workspacePath = workspacePath;
  }

  definition = {
    name: "browser",
    description: `Control a headless Chrome browser for interactive web automation. Use this when web_crawl fails (bot-blocked, login-gated, SPA) or when you need to interact with a page (click buttons, fill forms, scroll).

Workflow: start → navigate → snapshot (get element refs) → act (click/type using refs) → screenshot.

Actions:
- start: Launch browser session (required first)
- navigate url="...": Go to URL
- snapshot: Get page content as text with numbered element refs
- act ref="12" act="click": Interact with element (click/type/press/fill)
- act ref="5" act="type" value="search query": Type into input
- screenshot: Capture visual screenshot (saved to workspace)
- stop: Close browser session

IMPORTANT: Always start before navigating. Use snapshot refs for act commands.`,
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          description:
            "Action: start, stop, navigate, snapshot, act, screenshot, status",
        },
        url: {
          type: "string",
          description: "URL for navigate action",
        },
        ref: {
          type: "string",
          description: "Element ref number from snapshot (e.g. '5')",
        },
        act: {
          type: "string",
          description: "Interaction type: click, type, press, fill, hover",
        },
        value: {
          type: "string",
          description: "Text value for type/fill/press actions",
        },
      },
      required: ["action"],
    },
  };

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const action = args["action"] as string;
    const profile = DEFAULT_PROFILE;

    try {
      switch (action) {
        case "status":
          return this.handleStatus(profile);
        case "start":
          return await this.handleStart(profile);
        case "stop":
        case "close":
          return await this.handleStop(profile);
        case "navigate":
        case "open": {
          const url = args["url"] as string;
          if (!url) return "ERROR: url parameter is required for navigate.";
          return await this.handleNavigate(profile, url);
        }
        case "snapshot":
          return await this.handleSnapshot(profile);
        case "screenshot":
          return await this.handleScreenshot(profile, context);
        case "act":
          return await this.handleAct(profile, args);
        default:
          return `ERROR: Unknown action "${action}". Use: start, navigate, snapshot, act, screenshot, stop`;
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);

      // Auto-recover from stale profile lock
      if (msg.includes("already running") || msg.includes("SingletonLock")) {
        log.tool.warn(
          `[Browser] Stale lock detected for profile "${profile}" — cleaning up and retrying`,
        );
        await this.cleanProfileDir(profile);
        // Retry the start
        if (action === "start") {
          return await this.handleStart(profile);
        }
      }

      return `ERROR: ${msg}`;
    }
  }

  // ─── Actions ────────────────────────────────────────────────

  private handleStatus(profile: string): string {
    const p = this.profiles.get(profile);
    if (p) {
      return `Browser is running. Current page: ${p.page.url()}`;
    }
    return "Browser is not running. Use action=start to launch it.";
  }

  private async handleStart(profile: string): Promise<string> {
    // If we already have a running browser, return it
    if (this.profiles.has(profile)) {
      const p = this.profiles.get(profile)!;
      return `Browser already running. Current page: ${p.page.url()}`;
    }

    const userDataDir = join(this.workspacePath, ".browser-profiles", profile);
    if (!existsSync(userDataDir)) {
      mkdirSync(userDataDir, { recursive: true });
    }

    // Clean stale singleton locks before launching
    await this.cleanProfileDir(profile);

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

    // Set a reasonable user agent
    await page.setUserAgent(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    );

    this.profiles.set(profile, { id: profile, browser, page });

    return "Browser started successfully. Use action=navigate with a url to go to a page.";
  }

  private async handleStop(profile: string): Promise<string> {
    const p = this.profiles.get(profile);
    if (!p) return "Browser was not running.";

    try {
      await p.browser.close();
    } catch {
      // Already closed
    }
    this.profiles.delete(profile);
    return "Browser stopped.";
  }

  private async handleNavigate(
    profile: string,
    url: string,
  ): Promise<string> {
    // Validate URL
    try {
      const parsed = new URL(url);
      if (!["http:", "https:"].includes(parsed.protocol)) {
        return "ERROR: Only http:// and https:// URLs are supported.";
      }
      url = parsed.toString();
    } catch {
      return `ERROR: Invalid URL: ${url}`;
    }

    const p = this.getProfile(profile);
    if (!p) return this.notStartedMsg();

    try {
      await p.page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: NAV_TIMEOUT,
      });
      // Wait a bit for dynamic content
      await new Promise((r) => setTimeout(r, 1500));

      const title = await p.page.title();

      // Detect common bot-protection pages
      const bodyText = await p.page.evaluate(
        () => (document.body?.innerText || "").slice(0, 500),
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
          `The browser tool (Puppeteer) was detected as a bot.\n` +
          `FALLBACK: Use the computer_use tool instead — it controls the REAL mouse and keyboard:\n` +
          `  1. computer_use(action:'open_url', text:'${url}')\n` +
          `  2. computer_use(action:'wait', amount:3000)\n` +
          `  3. computer_use(action:'analyze_screen') to read the page\n` +
          `This bypasses all bot detection because it uses native OS input, not automation protocols.`
        );
      }

      return `Navigated to: ${url}\nPage title: ${title}\n\nUse action=snapshot to see the page content and interactive elements.`;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("timeout")) {
        return `Page took too long to load (>${NAV_TIMEOUT / 1000}s). The page may still be partially loaded. Try action=snapshot to see what's there.`;
      }
      return `Navigation failed: ${msg}`;
    }
  }

  private async handleSnapshot(profile: string): Promise<string> {
    const p = this.getProfile(profile);
    if (!p) return this.notStartedMsg();

    // Wait for page to stabilize after navigation
    try {
      await p.page.waitForFunction(() => document.readyState === "complete", {
        timeout: 5000,
      });
    } catch {
      // Timeout is OK — page may be streaming
    }

    const url = p.page.url();
    const title = await p.page.title();

    // Detect bot blocks on snapshot too (page may have redirected after navigate)
    const quickCheck = await p.page.evaluate(
      () => (document.body?.innerText || "").slice(0, 300),
    );
    if (
      title.includes("Security Checkpoint") ||
      title.includes("Just a moment") ||
      quickCheck.includes("verifying your browser") ||
      quickCheck.includes("Verify you are human")
    ) {
      return (
        `BLOCKED: This page has bot protection ("${title}").\n` +
        `URL: ${url}\n` +
        `FALLBACK: Use computer_use tool instead (open_url → wait → analyze_screen). ` +
        `It uses real mouse/keyboard and bypasses all bot detection.`
      );
    }

    // Extract visible text content (cleaned)
    const pageText = await p.page.evaluate(() => {
      // Remove noise elements
      const noise = document.querySelectorAll(
        "script, style, noscript, svg, iframe, nav",
      );
      noise.forEach((el) => el.remove());

      const body = document.body;
      if (!body) return "(empty page)";

      // Get cleaned text
      return (body.innerText || body.textContent || "")
        .replace(/[\r\n]+/g, "\n")
        .replace(/[ \t]+/g, " ")
        .trim()
        .slice(0, 8000);
    });

    // Get interactive elements with ref numbers
    const elements = await p.page.evaluate(() => {
      const selectors =
        'a, button, input, select, textarea, [role="button"], [role="link"], [role="tab"]';
      const all = Array.from(document.querySelectorAll(selectors));
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
        // Skip invisible elements
        if (rect.width === 0 || rect.height === 0) continue;

        const tag = el.tagName.toLowerCase();
        const inputEl = el as HTMLInputElement;
        const text = (el.textContent || "").trim().slice(0, 80);
        const placeholder = inputEl.placeholder || "";
        const type = inputEl.type || "";
        const name = inputEl.name || el.getAttribute("aria-label") || "";

        results.push({
          ref: i + 1,
          tag,
          type,
          text: text || placeholder || name || "(unlabeled)",
          placeholder,
          name,
        });
      }
      return results;
    });

    // Build human-readable output
    const lines: string[] = [];
    lines.push(`## Page: ${title}`);
    lines.push(`URL: ${url}\n`);

    if (pageText) {
      // Truncate page text to leave room for elements
      const truncText =
        pageText.length > 4000
          ? pageText.slice(0, 4000) + "\n...[text truncated]"
          : pageText;
      lines.push(`### Content\n${truncText}\n`);
    }

    if (elements.length > 0) {
      lines.push(`### Interactive Elements`);
      lines.push(
        `Use these ref numbers with action=act to interact:\n`,
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
      lines.push("(No interactive elements found on this page)");
    }

    return lines.join("\n");
  }

  private async handleScreenshot(
    profile: string,
    context: ToolContext,
  ): Promise<string> {
    const p = this.getProfile(profile);
    if (!p) return this.notStartedMsg();

    const dir = join(context.cwd || this.workspacePath, "screenshots");
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });

    const filename = `browser_${Date.now()}.png`;
    const filepath = join(dir, filename);

    await p.page.screenshot({ path: filepath, fullPage: false });

    return `Screenshot saved: ${filepath}\nUse send_file to deliver it to the user.`;
  }

  private async handleAct(
    profile: string,
    args: Record<string, unknown>,
  ): Promise<string> {
    const p = this.getProfile(profile);
    if (!p) return this.notStartedMsg();

    const actType = (args["act"] as string) || "click";
    const ref = args["ref"] as string;
    const value = (args["value"] as string) || "";

    if (!ref) return "ERROR: ref parameter is required. Use snapshot to get element ref numbers.";

    const refNum = parseInt(ref.replace(/\D/g, ""), 10);
    if (isNaN(refNum) || refNum < 1)
      return `ERROR: Invalid ref "${ref}". Use a number from the snapshot.`;

    const ELEMENT_SELECTOR =
      'a, button, input, select, textarea, [role="button"], [role="link"], [role="tab"]';

    // For type/fill/click/hover — use page.evaluate to find and interact with
    // the element in a single execution context (avoids "Argument should belong
    // to the same JavaScript world" errors after DOM changes)
    switch (actType) {
      case "click": {
        const clickNavPromise = p.page
          .waitForNavigation({ waitUntil: "domcontentloaded", timeout: 5000 })
          .catch(() => null);
        const found = await p.page.evaluate(
          (sel: string, idx: number) => {
            const el = document.querySelectorAll(sel)[idx - 1] as HTMLElement | undefined;
            if (!el) return false;
            el.click();
            return true;
          },
          ELEMENT_SELECTOR,
          refNum,
        );
        if (!found) return `ERROR: Element ref ${refNum} not found. Run snapshot again.`;
        await clickNavPromise;
        await new Promise((r) => setTimeout(r, 500));
        return `Clicked element [ref:${refNum}]. Current page: ${p.page.url()}\nUse snapshot to see updated content.`;
      }

      case "type": {
        if (!value) return "ERROR: value parameter is required for type action.";
        // Focus the element, clear it, then type using keyboard (works across contexts)
        const focused = await p.page.evaluate(
          (sel: string, idx: number) => {
            const el = document.querySelectorAll(sel)[idx - 1] as HTMLElement | undefined;
            if (!el) return false;
            el.focus();
            // Select all existing text so typing replaces it
            if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
              el.select();
            }
            return true;
          },
          ELEMENT_SELECTOR,
          refNum,
        );
        if (!focused) return `ERROR: Element ref ${refNum} not found. Run snapshot again.`;
        // Type character by character via keyboard API (resilient to context changes)
        await p.page.keyboard.type(value, { delay: 20 });
        return `Typed "${value}" into element [ref:${refNum}]. Use act with act=press value=Enter to submit.`;
      }

      case "fill": {
        if (!value) return "ERROR: value parameter is required for fill action.";
        const filled = await p.page.evaluate(
          (sel: string, idx: number, val: string) => {
            const el = document.querySelectorAll(sel)[idx - 1] as HTMLInputElement | undefined;
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
        if (!filled) return `ERROR: Element ref ${refNum} not found. Run snapshot again.`;
        return `Filled element [ref:${refNum}] with "${value}".`;
      }

      case "press": {
        const key = value || "Enter";
        const navPromise =
          key === "Enter"
            ? p.page
                .waitForNavigation({ waitUntil: "domcontentloaded", timeout: 10000 })
                .catch(() => null)
            : null;
        await p.page.keyboard.press(key as any);
        if (navPromise) await navPromise;
        await new Promise((r) => setTimeout(r, 1000));
        return `Pressed ${key}. Current page: ${p.page.url()}\nUse snapshot to see updated content.`;
      }

      case "hover": {
        const hovered = await p.page.evaluate(
          (sel: string, idx: number) => {
            const el = document.querySelectorAll(sel)[idx - 1] as HTMLElement | undefined;
            if (!el) return false;
            el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
            return true;
          },
          ELEMENT_SELECTOR,
          refNum,
        );
        if (!hovered) return `ERROR: Element ref ${refNum} not found. Run snapshot again.`;
        return `Hovered over element [ref:${refNum}]. Use snapshot to see any tooltip or dropdown.`;
      }

      case "select": {
        if (!value) return "ERROR: value parameter is required for select action.";
        const selected = await p.page.evaluate(
          (sel: string, idx: number, val: string) => {
            const el = document.querySelectorAll(sel)[idx - 1] as HTMLSelectElement | undefined;
            if (!el) return false;
            el.value = val;
            el.dispatchEvent(new Event("change", { bubbles: true }));
            return true;
          },
          ELEMENT_SELECTOR,
          refNum,
          value,
        );
        if (!selected) return `ERROR: Element ref ${refNum} not found. Run snapshot again.`;
        return `Selected "${value}" in dropdown [ref:${refNum}].`;
      }

      default:
        return `ERROR: Unknown act type "${actType}". Use: click, type, fill, press, hover, select`;
    }
  }

  // ─── Helpers ────────────────────────────────────────────────

  private getProfile(profile: string): BrowserProfile | undefined {
    return this.profiles.get(profile);
  }

  private notStartedMsg(): string {
    return "ERROR: Browser not started. Use action=start first.";
  }

  private async cleanProfileDir(profile: string): Promise<void> {
    const dir = join(this.workspacePath, ".browser-profiles", profile);
    // Remove Chrome's singleton lock files that prevent launch after crash
    for (const lock of ["SingletonLock", "SingletonSocket", "SingletonCookie"]) {
      const lockPath = join(dir, lock);
      try {
        if (existsSync(lockPath)) rmSync(lockPath, { force: true });
      } catch {
        // Non-fatal
      }
    }
  }

  async cleanup(): Promise<void> {
    for (const [profile] of this.profiles) {
      await this.handleStop(profile);
    }
  }
}
