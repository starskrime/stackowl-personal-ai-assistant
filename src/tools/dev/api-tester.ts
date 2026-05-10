import type { ToolImplementation } from "../registry.js";
import { log } from "../../logger.js";

const MAX_BODY_LENGTH = 5000;

export const APITesterTool: ToolImplementation = {
  definition: {
    name: "api_tester",
    description:
      "Test HTTP APIs — send requests with any method, headers, and body. Returns status, headers, and response.",
    parameters: {
      type: "object",
      properties: {
        method: {
          type: "string",
          enum: ["GET", "POST", "PUT", "DELETE", "PATCH"],
          description: "HTTP method.",
        },
        url: {
          type: "string",
          description: "Request URL.",
        },
        headers: {
          type: "string",
          description:
            'Optional headers as a JSON string, e.g. \'{"Authorization":"Bearer xxx"}\'.',
        },
        body: {
          type: "string",
          description: "Optional request body string.",
        },
      },
      required: ["method", "url"],
    },
  },

  async execute(args, _context) {
    const method = args.method as string;
    const url = args.url as string;
    const safeUrl = (() => {
      try { const u = new URL(url); return u.origin + u.pathname; } catch { return "[invalid-url]"; }
    })();
    const headersRaw = args.headers as string | undefined;
    const body = args.body as string | undefined;

    // 1. ENTRY
    log.tool.debug("api_tester.execute: entry", { method, url: safeUrl, hasHeaders: !!headersRaw, hasBody: !!body });

    try {
      let parsedHeaders: Record<string, string> = {};
      if (headersRaw) {
        try {
          parsedHeaders = JSON.parse(headersRaw);
        } catch (err) {
          log.tool.warn("api_tester.execute: headers parse failed", err);
          return "Error: headers must be a valid JSON string.";
        }
      }

      // 2. DECISION — auth scheme used
      const authHeader = parsedHeaders["Authorization"] ?? parsedHeaders["authorization"];
      const authScheme = authHeader
        ? authHeader.toLowerCase().startsWith("bearer ") ? "bearer"
        : authHeader.toLowerCase().startsWith("basic ") ? "basic"
        : "custom"
        : "none";
      log.tool.debug("api_tester.execute: request prepared", { method, url: safeUrl, authScheme });

      const fetchOptions: RequestInit = {
        method,
        headers: parsedHeaders,
        signal: AbortSignal.timeout(15000),
      };

      if (body && method !== "GET") {
        fetchOptions.body = body;
      }

      // 3. STEP — HTTP request sent
      const reqStart = Date.now();
      const resp = await fetch(url, fetchOptions);
      const latencyMs = Date.now() - reqStart;

      log.tool.debug("api_tester.execute: response received", { status: resp.status, latencyMs });

      // Collect response headers
      const respHeaders: string[] = [];
      resp.headers.forEach((value, key) => {
        respHeaders.push(`  ${key}: ${value}`);
      });

      let respBody: string;
      try {
        respBody = await resp.text();
      } catch (err) {
        log.tool.warn("api_tester.execute: response body read failed", err);
        respBody = "(could not read response body)";
      }

      if (respBody.length > MAX_BODY_LENGTH) {
        respBody =
          respBody.slice(0, MAX_BODY_LENGTH) +
          `\n... (truncated, ${respBody.length} chars total)`;
      }

      const result = [
        `Status: ${resp.status} ${resp.statusText}`,
        `\nResponse Headers:\n${respHeaders.join("\n")}`,
        `\nBody:\n${respBody}`,
      ].join("\n");

      // 4. EXIT
      log.tool.debug("api_tester.execute: exit", { success: true, resultLen: result.length });
      return result;
    } catch (e) {
      log.tool.error("api_tester.execute: request failed", e instanceof Error ? e : new Error(String(e)), { method, url: safeUrl });
      return `api_tester error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
