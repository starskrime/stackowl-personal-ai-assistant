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
    try {
      this.browser = await (puppeteer as any).launch({
        headless: true,
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

  async fetch(url: string, timeoutMs = 25_000): Promise<PuppeteerFetchResult> {
    if (!this.browser || !this.sessionPool) {
      throw new Error("PuppeteerFetcher not initialized — call init() first");
    }
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
