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
 *   - findExecutable() checks ELF machine type vs process.arch before using
 *     puppeteer's bundled Chrome — falls back to system Chromium on ARM64
 *     because puppeteer downloads x86-64 Chrome by default.
 */

import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import type { Browser, BrowserContext } from "puppeteer";
import { SessionPool, Session } from "@crawlee/core";
import { existsSync } from "node:fs";
import { open } from "node:fs/promises";
import { executablePath as puppeteerExePath } from "puppeteer";

(puppeteer as any).use(StealthPlugin());

// ELF machine-type constants (offset 0x12, little-endian uint16)
const ELF_MACHINE_X86_64 = 0x3e;
const ELF_MACHINE_AARCH64 = 0xb7;

// Candidate system Chromium paths (Debian/Ubuntu ARM and x86)
const SYSTEM_CHROMIUM_CANDIDATES = [
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
];

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
   * Finds the best Chromium executable for the current architecture.
   * Reads the ELF machine-type header of puppeteer's bundled Chrome to detect
   * an arch mismatch (e.g. x86-64 binary on ARM64 Jetson), then falls back to
   * system Chromium when there is one.
   */
  private async findExecutable(): Promise<string | null> {
    // 1. Try puppeteer's bundled Chrome — check ELF arch matches process.arch
    try {
      const bundled = puppeteerExePath();
      if (bundled && existsSync(bundled)) {
        const fd = await open(bundled, "r");
        const buf = Buffer.alloc(20);
        await fd.read(buf, 0, 20, 0);
        await fd.close();
        // ELF magic bytes: 0x7f 'E' 'L' 'F'
        if (buf[0] === 0x7f && buf[1] === 0x45 && buf[2] === 0x4c && buf[3] === 0x46) {
          const machineType = buf.readUInt16LE(0x12);
          const archOk =
            (process.arch === "x64"   && machineType === ELF_MACHINE_X86_64) ||
            (process.arch === "arm64" && machineType === ELF_MACHINE_AARCH64);
          if (archOk) return bundled;
          process.stderr.write(
            `[puppeteer] bundled Chrome is ${machineType === ELF_MACHINE_X86_64 ? "x86-64" : "unknown"} ` +
            `but process.arch=${process.arch} — falling back to system Chromium\n`,
          );
        }
      }
    } catch (err) {
      process.stderr.write(`[puppeteer] could not read bundled Chrome ELF header: ${err instanceof Error ? err.message : String(err)}\n`);
    }

    // 2. Fall back to the first system Chromium that exists
    for (const candidate of SYSTEM_CHROMIUM_CANDIDATES) {
      if (existsSync(candidate)) {
        process.stderr.write(`[puppeteer] using system Chromium: ${candidate}\n`);
        return candidate;
      }
    }

    return null;
  }

  async init(): Promise<void> {
    const execPath = await this.findExecutable();
    if (!execPath) {
      throw new Error("No compatible Chromium executable found (bundled arch mismatch, no system Chromium)");
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
          process.stderr.write(`[puppeteer] waitForSelector "${waitForSelector}" timed out: ${err instanceof Error ? err.message : String(err)}\n`);
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
   * Returns true if a compatible Chromium executable is available for this arch.
   * Checks bundled Chrome ELF arch first, then system Chromium candidates.
   */
  async probe(): Promise<boolean> {
    try {
      return (await this.findExecutable()) !== null;
    } catch (err) {
      process.stderr.write(`[puppeteer] probe failed: ${err instanceof Error ? err.message : String(err)}\n`);
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
