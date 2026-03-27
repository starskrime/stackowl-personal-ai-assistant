import type { ToolImplementation } from "../registry.js";

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
    const headersRaw = args.headers as string | undefined;
    const body = args.body as string | undefined;

    try {
      let parsedHeaders: Record<string, string> = {};
      if (headersRaw) {
        try {
          parsedHeaders = JSON.parse(headersRaw);
        } catch {
          return "Error: headers must be a valid JSON string.";
        }
      }

      const fetchOptions: RequestInit = {
        method,
        headers: parsedHeaders,
        signal: AbortSignal.timeout(15000),
      };

      if (body && method !== "GET") {
        fetchOptions.body = body;
      }

      const resp = await fetch(url, fetchOptions);

      // Collect response headers
      const respHeaders: string[] = [];
      resp.headers.forEach((value, key) => {
        respHeaders.push(`  ${key}: ${value}`);
      });

      let respBody: string;
      try {
        respBody = await resp.text();
      } catch {
        respBody = "(could not read response body)";
      }

      if (respBody.length > MAX_BODY_LENGTH) {
        respBody =
          respBody.slice(0, MAX_BODY_LENGTH) +
          `\n... (truncated, ${respBody.length} chars total)`;
      }

      return [
        `Status: ${resp.status} ${resp.statusText}`,
        `\nResponse Headers:\n${respHeaders.join("\n")}`,
        `\nBody:\n${respBody}`,
      ].join("\n");
    } catch (e) {
      return `api_tester error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
