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
 */

import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import type { Browser, BrowserContext } from "puppeteer";
import { SessionPool, Session } from "@crawlee/core";
import { existsSync } from "node:fs";
import { executablePath as puppeteerExePath } from "puppeteer";

(puppeteer as any).use(StealthPlugin());

export interface PuppeteerFetchResult {
  html: string;
  finalUrl: string;
  status: number;
}

export class PuppeteerFetcher {
  private browser: Browser | null = null;
  private sessionPool: SessionPool | null = null;

  async init(): Promise<void> {
    this.sessionPool = await SessionPool.open({
      maxPoolSize: 5,
      createSessionFunction: (pool) =>
        new Session({ sessionPool: pool, userData: {} }),
    });
    this.browser = await (puppeteer as any).launch({
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
      ],
    });
  }

  async fetch(url: string, timeoutMs = 25_000): Promise<PuppeteerFetchResult> {
    if (!this.browser) {
      throw new Error("PuppeteerFetcher not initialized — call init() first");
    }
    const session = await this.sessionPool!.getSession();
    const context: BrowserContext = await this.browser.createBrowserContext();
    const page = await context.newPage();
    try {
      const origin = new URL(url).origin;
      const cookies = session.getCookies(origin);
      if (cookies.length) {
        await context.setCookie(...(cookies as any[]));
      }

      const response = await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: timeoutMs,
      });
      const html = await page.content();

      // Persist cookies back into session for next request
      const updatedCookies = await page.cookies();
      if (updatedCookies.length) {
        session.setCookies(updatedCookies as any[], origin);
      }
      session.markGood();

      return {
        html,
        finalUrl: page.url(),
        status: response?.status() ?? 200,
      };
    } catch (err) {
      session.markBad();
      throw err;
    } finally {
      await context.close();
    }
  }

  async probe(): Promise<boolean> {
    try {
      const path = puppeteerExePath();
      return !!path && existsSync(path);
    } catch {
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
