/**
 * StackOwl — Web Fetch Tool
 *
 * Fetches and cleans text from any URL. Uses the smart fetch layer
 * which escalates scrapling → camofox (Obscura reserved) when blocked.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";
import { webFetchEnvelope } from "../browser/smart-fetch.js";

export const WebFetchTool: ToolImplementation = {
  definition: {
    name: "web_fetch",
    description:
      "Fetch and extract content from a URL. Tries scrapling, then camofox (Obscura is reserved). Returns a structured envelope with the page text or a typed error code. Use hint:'anti-bot' if you already have evidence the site uses Cloudflare/PerimeterX/Akamai — this skips the lightweight tier. On ALL_TIERS_UNAVAILABLE or BLOCKED_BY_ANTI_BOT, escalate to live_browser.",
    parameters: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description:
            "Full URL to fetch (e.g. https://example.com/article). Must start with http:// or https://",
        },
        hint: {
          type: "string",
          enum: ["anti-bot"],
          description: "Skip Tier 1 (scrapling) and start with camofox.",
        },
      },
      required: ["url"],
    },
    capabilities: ["web_fetch", "http_request"],
    executionPolicy: { timeoutMs: 30_000, maxRetries: 0 },
  },

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const { serializeWebToolResult } = await import("../browser/envelope.js");
    let url = args["url"] as string;
    const hint = args["hint"] as "anti-bot" | undefined;

    // 1. ENTRY
    log.tool.debug("web.execute: entry", { url, hint: hint ?? "none" });

    if (!url) {
      return serializeWebToolResult({
        success: false,
        error: { code: "INVALID_URL", message: "URL is required", attemptedTiers: [] },
      });
    }
    try {
      const parsedUrl = new URL(url);
      if (!["http:", "https:"].includes(parsedUrl.protocol)) {
        return serializeWebToolResult({
          success: false,
          error: { code: "INVALID_URL", message: "Only http(s) URLs are supported", attemptedTiers: [] },
        });
      }
      url = parsedUrl.toString();
    } catch (err) {
      log.tool.warn("web: URL parse failed", err);
      return serializeWebToolResult({
        success: false,
        error: { code: "INVALID_URL", message: `Invalid URL: ${url}`, attemptedTiers: [] },
      });
    }

    // 2. DECISION — fetch strategy
    const strategy = hint === "anti-bot" ? "camofox" : "scrapling→camofox";
    log.tool.debug("web.execute: fetch strategy chosen", { chosen: strategy, hint: hint ?? "none", url });

    try {
      // 3. STEP — fetch
      log.tool.debug("web.execute: fetching", { url, strategy });
      const envelope = await webFetchEnvelope(url, {
        scrapling: (context.engineContext as any)?.scrapling,
        classifier: context.classifier,
        puppeteer: context.puppeteer,
        bus: (context.engineContext as any)?.eventBus,
        hint,
      });

      // 3. STEP — response received
      const envelopeSuccess = envelope.success;
      const contentLen = envelopeSuccess && (envelope as any).content ? String((envelope as any).content).length : 0;
      log.tool.debug("web.execute: response received", { success: envelopeSuccess, contentLen });

      const serialized = serializeWebToolResult(envelope);
      // 4. EXIT
      log.tool.debug("web.execute: exit", { success: envelopeSuccess, resultLen: serialized.length });
      return serialized;
    } catch (error) {
      // ERROR
      log.tool.error("web.execute: fetch failed", error instanceof Error ? error : new Error(String(error)), { url });
      return serializeWebToolResult({
        success: false,
        error: {
          code: "INTERNAL_ERROR",
          message: error instanceof Error ? error.message : String(error),
          attemptedTiers: [],
        },
      });
    }
  },
};

