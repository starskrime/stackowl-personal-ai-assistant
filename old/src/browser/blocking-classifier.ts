import { createHash } from "node:crypto";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ModelProvider } from "../providers/base.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";

export interface ClassifyInput {
  url: string;
  httpStatus: number;
  bodyPreview: string;
  headers?: Record<string, string>;
}

export interface BlockingClassification {
  blocked: boolean;
  reason?: "cloudflare" | "captcha" | "paywall" | "rate-limit" | "access-denied" | "other";
  confidence: number;
  source: "cache" | "router" | "fallback";
}

interface CacheEntry { v: BlockingClassification; expiresAt: number }

const CLASSIFY_BUDGET_MS = 200;
const CACHE_TTL_MS = 60 * 60 * 1000;
const CACHE_CAP = 1000;

const SYSTEM_PROMPT = `You are a web-blocking classifier. Given an HTTP response (status, body preview, headers), reply ONLY with JSON:
{"blocked": boolean, "reason": "cloudflare"|"captcha"|"paywall"|"rate-limit"|"access-denied"|"other", "confidence": number}
Confidence is 0..1. If uncertain, set "blocked": false.`;

export class BlockingClassifier {
  private cache = new Map<string, CacheEntry>();

  constructor(
    private router: IntelligenceRouter,
    private providers: Map<string, ModelProvider>,
    private bus: GatewayEventBus,
  ) {}

  async classify(input: ClassifyInput): Promise<BlockingClassification> {
    const start = Date.now();
    const key = this.cacheKey(input);
    const hit = this.cache.get(key);
    if (hit && hit.expiresAt > Date.now()) {
      const v: BlockingClassification = { ...hit.v, source: "cache" };
      this.emit(input, v, Date.now() - start);
      return v;
    }

    let resolved: { provider: string; model: string };
    try { resolved = this.router.resolve("classification") as any; }
    catch { return this.fallback(input, start); }
    const provider = this.providers.get(resolved.provider);
    if (!provider) return this.fallback(input, start);

    const userContent = `URL: ${input.url}\nHTTP status: ${input.httpStatus}\nBody preview (first 2KB):\n${input.bodyPreview.slice(0, 2048)}`;
    const budget = new Promise<{ timeout: true }>(r => setTimeout(() => r({ timeout: true }), CLASSIFY_BUDGET_MS));
    const call = provider.chat([
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: userContent },
    ], resolved.model, { temperature: 0 });

    let raced: any;
    try { raced = await Promise.race([call, budget]); }
    catch { return this.fallback(input, start); }
    if (raced && (raced as any).timeout) return this.fallback(input, start);

    const parsed = this.parse((raced as { content: string }).content);
    if (!parsed) return this.fallback(input, start);
    const v: BlockingClassification = { ...parsed, source: "router" };
    this.cacheSet(key, v);
    this.emit(input, v, Date.now() - start);
    return v;
  }

  private parse(s: string): Omit<BlockingClassification, "source"> | null {
    try {
      const m = s.match(/\{[\s\S]*\}/);
      if (!m) return null;
      const p = JSON.parse(m[0]) as any;
      if (typeof p.blocked !== "boolean") return null;
      const allowed = ["cloudflare","captcha","paywall","rate-limit","access-denied","other"];
      const reason = allowed.includes(p.reason) ? p.reason : "other";
      const confidence = typeof p.confidence === "number" ? Math.max(0, Math.min(1, p.confidence)) : 0;
      return { blocked: p.blocked, reason, confidence };
    } catch { return null; }
  }

  private fallback(input: ClassifyInput, start: number): BlockingClassification {
    const v: BlockingClassification = { blocked: false, confidence: 0, source: "fallback" };
    this.emit(input, v, Date.now() - start);
    return v;
  }

  private cacheKey(i: ClassifyInput): string {
    const host = new URL(i.url).host;
    const bodyHash = createHash("sha1").update(i.bodyPreview.slice(0, 2048)).digest("hex").slice(0, 12);
    return `${host}|${i.httpStatus}|${bodyHash}`;
  }

  private cacheSet(key: string, v: BlockingClassification): void {
    if (this.cache.size >= CACHE_CAP) {
      const first = this.cache.keys().next().value;
      if (first) this.cache.delete(first);
    }
    this.cache.set(key, { v, expiresAt: Date.now() + CACHE_TTL_MS });
  }

  private emit(input: ClassifyInput, v: BlockingClassification, latency: number): void {
    try {
      this.bus.emit({
        type: "web:blocking_classified",
        url: input.url, source: v.source, latency, blocked: v.blocked, reason: v.reason ?? null,
      } as any);
    } catch { /* fail-open on bus */ }
  }
}
