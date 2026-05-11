/**
 * StackOwl — PuppeteerFetcher (Element 16d Tier 3)
 *
 * Warm browser singleton with stealth plugin + SessionPool cookie rotation.
 * Tier 3 in the web-fetch escalation chain:
 *   Tier 1 scrapling → Tier 2 camofox → Tier 3 puppeteer → Tier 4 obscura
 *
 * Design decisions:
 *   - puppeteer-extra (not bare puppeteer) — required by stealth plugin
 *   - SessionPool.open() async factory — new SessionPool() doesn't initialize
 *   - waitUntil: "domcontentloaded" — networkidle2 hangs on Amazon et al.
 *   - Single Browser shared across requests + new BrowserContext per request
 *   - Stealth plugin manages User-Agent — no custom UA set here
 *
 * Multi-architecture note:
 *   Google's Chrome for Testing CDN publishes Linux binaries for x86-64 only.
 *   Both the `linux` and `linux_arm` puppeteer platform entries download the
 *   same linux64/chrome-linux64.zip — there is no ARM64 Linux Chrome from Google.
 *   On Linux ARM we skip the bundled binary (skipDownload in .puppeteerrc.cjs)
 *   and findExecutable() falls back to system Chromium.
 *   macOS ARM64 (mac_arm) does have a native binary from Google and works normally.
 *   Windows win32/win64 work normally.
 */

import { log } from "../logger.js";
import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import type { Browser, BrowserContext } from "puppeteer";
import { SessionPool, Session } from "@crawlee/core";
import { existsSync } from "node:fs";
import { open } from "node:fs/promises";
import { executablePath as puppeteerExePath } from "puppeteer";

(puppeteer as any).use(StealthPlugin());

// ELF machine-type constants (ELF header offset 0x12, little-endian uint16).
// Used to verify the bundled Chrome binary matches the current CPU on Linux.
const ELF_MACHINE_X86_64  = 0x3e;
const ELF_MACHINE_AARCH64 = 0xb7;
const ELF_MACHINE_ARM32   = 0x28;

// node `process.arch` → expected ELF machine type
const ARCH_TO_ELF: Partial<Record<NodeJS.Architecture, number>> = {
  x64:   ELF_MACHINE_X86_64,
  arm64: ELF_MACHINE_AARCH64,
  arm:   ELF_MACHINE_ARM32,
};

// System browser candidates per platform, in preference order.
// Paths are checked in order; first existing file wins.
const SYSTEM_CANDIDATES: Partial<Record<NodeJS.Platform, string[]>> = {
  linux: [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/snap/bin/chromium",              // Ubuntu snap
    "/usr/bin/brave-browser",
    "/usr/bin/microsoft-edge",
    "/usr/bin/microsoft-edge-stable",
  ],
  darwin: [
    // Homebrew ARM (Apple Silicon)
    "/opt/homebrew/bin/chromium",
    // Homebrew Intel
    "/usr/local/bin/chromium",
    // Installed .app bundles
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  ],
  win32: [
    // x64 Windows Program Files
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files\\Chromium\\Application\\chrome.exe",
    "C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    // Per-user install (LOCALAPPDATA may be undefined in some envs)
    ...(process.env.LOCALAPPDATA
      ? [
          `${process.env.LOCALAPPDATA}\\Google\\Chrome\\Application\\chrome.exe`,
          `${process.env.LOCALAPPDATA}\\Chromium\\Application\\chrome.exe`,
        ]
      : []),
  ],
};

export interface PuppeteerFetchResult {
  html: string;
  finalUrl: string;
  status: number;
}

export interface PuppeteerFetchOptions {
  timeoutMs?: number;
  waitForSelector?: string;
  waitForSelectorTimeout?: number;
}

export class PuppeteerFetcher {
  private browser: Browser | null = null;
  private sessionPool: SessionPool | null = null;

  /**
   * Verify the ELF machine type in a Linux binary matches the current CPU arch.
   * Returns true if compatible, false on mismatch. Throws on read error.
   */
  private async checkLinuxElfArch(binaryPath: string): Promise<boolean> {
    const fd = await open(binaryPath, "r");
    const buf = Buffer.alloc(20);
    await fd.read(buf, 0, 20, 0);
    await fd.close();
    // ELF magic: 0x7f 'E' 'L' 'F'
    if (buf[0] !== 0x7f || buf[1] !== 0x45 || buf[2] !== 0x4c || buf[3] !== 0x46) {
      return false; // not an ELF binary (e.g. shell wrapper)
    }
    const machineType = buf.readUInt16LE(0x12);
    const expected = ARCH_TO_ELF[process.arch as NodeJS.Architecture];
    return expected !== undefined && machineType === expected;
  }

  /**
   * Find the best Chromium/Chrome executable for the current platform and arch.
   *
   * Priority:
   *   1. Puppeteer's bundled Chrome — used on Linux x86-64, macOS x86-64,
   *      macOS ARM64, and Windows where Google provides the correct binary.
   *      On Linux the ELF machine type is verified to catch arch mismatches.
   *   2. System browser — used on Linux ARM (no Google ARM Linux Chrome exists)
   *      and as a fallback on all other platforms if the bundled binary is absent.
   *
   * Returns null when no compatible browser is found.
   */
  async findExecutable(): Promise<string | null> {
    const { platform, arch } = process;

    // On Linux ARM Google has no Chrome for Testing binary to offer.
    // Both `linux` and `linux_arm` puppeteer platforms download x86-64.
    // Skip the bundled check entirely on these arches.
    const skipBundled =
      platform === "linux" && (arch === "arm64" || arch === "arm");

    if (!skipBundled) {
      try {
        const bundled = puppeteerExePath();
        if (bundled && existsSync(bundled)) {
          if (platform === "linux") {
            // Verify ELF arch matches — catches any future puppeteer regression
            const ok = await this.checkLinuxElfArch(bundled);
            if (!ok) {
              const hint = arch === "x64" ? "x86-64" : arch;
              log.tool.warn("puppeteer.findExecutable: bundled Chrome wrong arch, falling back to system browser", { hint });
            } else {
              return bundled;
            }
          } else {
            // macOS and Windows: trust puppeteer's selection (it handles mac_arm correctly)
            return bundled;
          }
        }
      } catch (err) {
        log.tool.warn("puppeteer.findExecutable: bundled Chrome check failed", { err: err instanceof Error ? err.message : String(err) });
      }
    }

    // Fall back to system browser candidates
    const candidates = SYSTEM_CANDIDATES[platform as NodeJS.Platform] ?? [];
    for (const candidate of candidates) {
      if (existsSync(candidate)) {
        log.tool.info("puppeteer.findExecutable: using system browser", { candidate });
        return candidate;
      }
    }

    // Nothing found — emit a helpful install hint
    const installHints: Partial<Record<NodeJS.Platform, string>> = {
      linux:  "sudo apt install chromium   (Debian/Ubuntu/Jetson)",
      darwin: "brew install --cask chromium",
      win32:  "winget install Google.Chrome",
    };
    const hint = installHints[platform as NodeJS.Platform] ?? "install Chromium for your platform";
    log.tool.warn("puppeteer.findExecutable: no compatible browser found", { platform, arch, hint });
    return null;
  }

  async init(): Promise<void> {
    const execPath = await this.findExecutable();
    if (!execPath) {
      throw new Error(
        `PuppeteerFetcher: no compatible Chromium/Chrome found for ${process.platform}/${process.arch}`,
      );
    }

    this.sessionPool = await SessionPool.open({
      maxPoolSize: 5,
      createSessionFunction: (pool) =>
        new Session({ sessionPool: pool, userData: {} }),
    });
    try {
      this.browser = await (puppeteer as any).launch({
        headless: true,
        executablePath: execPath,
        timeout: 8_000,
        args: [
          "--no-sandbox",
          "--disable-setuid-sandbox",
          "--disable-blink-features=AutomationControlled",
        ],
      });
    } catch (err) {
      await this.sessionPool.teardown();
      this.sessionPool = null;
      throw err;
    }
  }

  async fetch(
    url: string,
    opts: PuppeteerFetchOptions = {},
  ): Promise<PuppeteerFetchResult> {
    if (!this.browser || !this.sessionPool) {
      throw new Error("PuppeteerFetcher not initialized — call init() first");
    }
    const {
      timeoutMs = 25_000,
      waitForSelector,
      waitForSelectorTimeout = 5_000,
    } = opts;

    const session = await this.sessionPool.getSession();
    let context: BrowserContext | null = null;
    try {
      context = await this.browser.createBrowserContext();
      const page = await context.newPage();

      const cookies = session.getCookies(new URL(url).origin);
      if (cookies.length) await context.setCookie(...(cookies as any[]));

      const response = await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: timeoutMs,
      });

      if (!response) throw new Error(`Navigation to ${url} failed — no response`);

      if (waitForSelector) {
        try {
          await page.waitForSelector(waitForSelector, {
            timeout: waitForSelectorTimeout,
          });
        } catch (err) {
          log.tool.warn("puppeteer.fetch: waitForSelector timed out", { waitForSelector, err: err instanceof Error ? err.message : String(err) });
          // proceed with whatever content loaded
        }
      }

      const html = await page.content();
      const updatedCookies = await page.cookies();
      session.setCookiesFromResponse(updatedCookies as any, new URL(url).origin);
      session.markGood();

      return {
        html,
        finalUrl: page.url(),
        status: response.status(),
      };
    } catch (err) {
      session.markBad();
      throw err;
    } finally {
      await context?.close();
    }
  }

  /**
   * Returns true if a compatible browser executable is available for this
   * platform and architecture. Does NOT launch Chrome.
   */
  async probe(): Promise<boolean> {
    try {
      return (await this.findExecutable()) !== null;
    } catch (err) {
      log.tool.warn("puppeteer.probe: error", { err: err instanceof Error ? err.message : String(err) });
      return false;
    }
  }

  async close(): Promise<void> {
    await this.sessionPool?.teardown();
    await this.browser?.close();
    this.browser = null;
    this.sessionPool = null;
  }
}
