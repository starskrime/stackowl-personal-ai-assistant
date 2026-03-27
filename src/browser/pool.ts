/**
 * StackOwl — Browser Pool
 *
 * Manages a pool of warm, persistent Chromium instances with stealth patches.
 * Browsers maintain cookies, localStorage, and fingerprint across requests.
 *
 * Key insight: browsing is an environment, not a tool call. Browsers stay alive
 * and build natural browsing history over time, making them progressively
 * harder to fingerprint as bots.
 */

import { mkdirSync, existsSync, rmSync } from "node:fs";
import { join } from "node:path";
import { execSync } from "node:child_process";
import { log } from "../logger.js";
import { findChrome } from "./chrome.js";

// ─── Types ───────────────────────────────────────────────────────

export interface BrowserPoolConfig {
  /** Number of browsers to keep warm. Default: 2. Each uses ~100-200MB RAM. */
  poolSize: number;
  /** Visit benign sites on startup to build cookie/fingerprint baseline. Default: true */
  warmUp: boolean;
  /** Apply stealth patches (webdriver, webgl, etc). Default: true */
  stealthMode: boolean;
  /** Base directory for persistent browser profiles. Default: '{workspace}/.browser-data' */
  userDataDir: string;
  /** Proxy URL (e.g. 'http://proxy:8080'). Optional. */
  proxy?: string;
  /** Run headless. Default: true */
  headless: boolean;
}

interface PooledBrowser {
  browser: import("puppeteer").Browser;
  profileDir: string;
  inUse: boolean;
  launchedAt: number;
  requestCount: number;
}

const DEFAULT_POOL_CONFIG: BrowserPoolConfig = {
  poolSize: 2,
  warmUp: true,
  stealthMode: true,
  userDataDir: "",
  headless: true,
};

// ─── Stealth patches applied to every new page ───────────────────

const STEALTH_SCRIPTS = [
  // Remove navigator.webdriver flag
  `Object.defineProperty(navigator, 'webdriver', { get: () => undefined });`,

  // Fix chrome.runtime (exists in real Chrome, missing in headless)
  `window.chrome = { runtime: {}, loadTimes: () => ({}), csi: () => ({}) };`,

  // Fix permissions.query for notifications
  `const origQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
   window.navigator.permissions.query = (params) =>
     params.name === 'notifications'
       ? Promise.resolve({ state: Notification.permission })
       : origQuery(params);`,

  // Fix plugins array (headless has 0 plugins)
  `Object.defineProperty(navigator, 'plugins', {
     get: () => [
       { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
       { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
       { name: 'Native Client', filename: 'internal-nacl-plugin' },
     ],
   });`,

  // Fix languages (headless sometimes returns empty)
  `Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });`,

  // Fix WebGL vendor/renderer (SwiftShader is a headless giveaway)
  `const getParam = WebGLRenderingContext.prototype.getParameter;
   WebGLRenderingContext.prototype.getParameter = function(param) {
     if (param === 37445) return 'Intel Inc.';
     if (param === 37446) return 'Intel Iris OpenGL Engine';
     return getParam.call(this, param);
   };`,
];

// ─── Implementation ─────────────────────────────────────────────

export class BrowserPool {
  private config: BrowserPoolConfig;
  private pool: PooledBrowser[] = [];
  private waitQueue: Array<(browser: PooledBrowser) => void> = [];
  private initialized = false;
  private shuttingDown = false;

  constructor(config?: Partial<BrowserPoolConfig>) {
    this.config = { ...DEFAULT_POOL_CONFIG, ...config };
  }

  /**
   * Launch browser pool and optionally warm up profiles.
   * Call this once during application bootstrap.
   */
  async init(): Promise<void> {
    if (this.initialized) return;

    mkdirSync(this.config.userDataDir, { recursive: true });

    log.engine.info(
      `[BrowserPool] Launching ${this.config.poolSize} browser(s)` +
        `${this.config.stealthMode ? " (stealth)" : ""}` +
        `${this.config.warmUp ? " with warm-up" : ""}...`,
    );

    const launchPromises: Promise<void>[] = [];
    for (let i = 0; i < this.config.poolSize; i++) {
      launchPromises.push(this.launchBrowser(i));
    }
    await Promise.allSettled(launchPromises);

    const alive = this.pool.filter((b) => b.browser.connected).length;
    if (alive === 0) {
      log.engine.warn(
        "[BrowserPool] No browsers launched — web fetching will fall back to HTTP only",
      );
      return;
    }

    log.engine.info(
      `[BrowserPool] ${alive}/${this.config.poolSize} browsers ready`,
    );

    if (this.config.warmUp) {
      await this.warmUp();
    }

    this.initialized = true;
  }

  /**
   * Acquire a browser from the pool. If none available, waits.
   * Caller MUST call release() when done.
   */
  async acquire(): Promise<PooledBrowser> {
    if (this.shuttingDown) throw new Error("BrowserPool is shutting down");

    // Find an idle browser
    const idle = this.pool.find((b) => !b.inUse && b.browser.connected);
    if (idle) {
      idle.inUse = true;
      idle.requestCount++;
      return idle;
    }

    // All busy — wait for one to be released
    return new Promise<PooledBrowser>((resolve) => {
      this.waitQueue.push(resolve);
    });
  }

  /**
   * Return a browser to the pool for reuse.
   */
  release(pooled: PooledBrowser): void {
    pooled.inUse = false;

    // If someone is waiting, give them this browser immediately
    if (this.waitQueue.length > 0) {
      const next = this.waitQueue.shift()!;
      pooled.inUse = true;
      pooled.requestCount++;
      next(pooled);
    }
  }

  /**
   * Get a new page from a pooled browser. Applies stealth scripts.
   * The page should be closed by the caller when done.
   */
  async getPage(pooled: PooledBrowser): Promise<import("puppeteer").Page> {
    const page = await pooled.browser.newPage();

    if (this.config.stealthMode) {
      for (const script of STEALTH_SCRIPTS) {
        await page.evaluateOnNewDocument(script);
      }
    }

    // Set realistic viewport and user agent
    await page.setViewport({ width: 1366, height: 768 });
    await page.setUserAgent(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    );

    return page;
  }

  /**
   * Gracefully shut down all browsers. Profiles are preserved on disk.
   */
  async shutdown(): Promise<void> {
    if (this.shuttingDown) return;
    this.shuttingDown = true;

    log.engine.info(
      `[BrowserPool] Shutting down ${this.pool.length} browser(s)...`,
    );

    // Reject any waiting acquires
    for (const waiter of this.waitQueue) {
      // Return a dummy that will fail — callers handle errors
      waiter(null as any);
    }
    this.waitQueue = [];

    const closePromises = this.pool.map(async (pooled) => {
      try {
        await pooled.browser.close();
      } catch {
        // Browser may have already crashed
      }
    });
    await Promise.allSettled(closePromises);
    this.pool = [];
    this.initialized = false;

    log.engine.info("[BrowserPool] All browsers closed");
  }

  /** Number of browsers currently idle */
  get availableCount(): number {
    return this.pool.filter((b) => !b.inUse && b.browser.connected).length;
  }

  /** Total number of browsers in the pool */
  get totalCount(): number {
    return this.pool.filter((b) => b.browser.connected).length;
  }

  get isReady(): boolean {
    return this.initialized && this.totalCount > 0;
  }

  // ─── Internal ─────────────────────────────────────────────────

  private async launchBrowser(index: number): Promise<void> {
    const profileDir = join(this.config.userDataDir, `profile-${index}`);

    // Clean stale lock files
    for (const lock of [
      "SingletonLock",
      "SingletonCookie",
      "SingletonSocket",
    ]) {
      const lockPath = join(profileDir, lock);
      if (existsSync(lockPath)) {
        try {
          rmSync(lockPath);
        } catch {
          /* ok */
        }
      }
    }

    // Kill any stale Chrome processes holding this profile directory.
    // This handles the case where a previous Node process was killed without
    // graceful shutdown and left Chrome running.
    try {
      execSync(
        `pkill -f 'chrome.*${profileDir.replace(/'/g, "'\\''")}' 2>/dev/null || true`,
        { stdio: "ignore" },
      );
    } catch {
      /* ignore */
    }

    mkdirSync(profileDir, { recursive: true });

    const chromePath = findChrome();
    const args = [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-blink-features=AutomationControlled",
      "--disable-infobars",
      "--window-size=1366,768",
    ];

    if (this.config.proxy) {
      args.push(`--proxy-server=${this.config.proxy}`);
    }

    try {
      const puppeteer = await import("puppeteer");
      const browser = await puppeteer.default.launch({
        headless: this.config.headless,
        executablePath: chromePath,
        userDataDir: profileDir,
        args,
        defaultViewport: null, // Use window-size from args
      });

      this.pool.push({
        browser,
        profileDir,
        inUse: false,
        launchedAt: Date.now(),
        requestCount: 0,
      });

      // Auto-restart crashed browsers
      browser.on("disconnected", () => {
        if (!this.shuttingDown) {
          log.engine.warn(
            `[BrowserPool] Browser ${index} disconnected — relaunching`,
          );
          const idx = this.pool.findIndex((b) => b.profileDir === profileDir);
          if (idx !== -1) this.pool.splice(idx, 1);
          this.launchBrowser(index).catch((err) => {
            log.engine.error(
              `[BrowserPool] Failed to relaunch browser ${index}: ${err}`,
            );
          });
        }
      });
    } catch (err) {
      log.engine.error(
        `[BrowserPool] Failed to launch browser ${index}: ` +
          `${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /**
   * Warm up browsers by visiting benign sites to build natural
   * cookie/fingerprint baselines. Makes subsequent requests look
   * like a real user who has been browsing.
   */
  private async warmUp(): Promise<void> {
    const warmUpUrls = [
      "https://www.google.com",
      "https://en.wikipedia.org/wiki/Main_Page",
    ];

    log.engine.info(
      `[BrowserPool] Warming up ${this.pool.length} browser(s)...`,
    );

    await Promise.allSettled(
      this.pool.map(async (pooled) => {
        const page = await this.getPage(pooled);
        try {
          for (const url of warmUpUrls) {
            try {
              await page.goto(url, {
                waitUntil: "domcontentloaded",
                timeout: 10000,
              });
              // Brief pause to look human
              await new Promise((r) =>
                setTimeout(r, 500 + Math.random() * 1000),
              );
            } catch {
              // Non-fatal: warm-up failure doesn't prevent operation
            }
          }
        } finally {
          await page.close().catch(() => {});
        }
      }),
    );

    log.engine.info("[BrowserPool] Warm-up complete");
  }
}
