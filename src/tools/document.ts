// src/tools/document.ts
import { readFile } from "node:fs/promises";
import { extname } from "node:path";
import type { ToolImplementation, ToolContext } from "./registry.js";

export const DocumentTool: ToolImplementation = {
  definition: {
    name: "document",
    description:
      "Parse a document file (PDF, DOCX, Markdown, plain text) and extract text, tables, and metadata. " +
      'Example: document(action: "parse", filePath: "/tmp/report.pdf")',
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["parse", "extract_tables", "metadata"],
          description: "What to extract from the document.",
        },
        filePath: {
          type: "string",
          description: "Absolute path to the document file.",
        },
      },
      required: ["action", "filePath"],
    },
    capabilities: ["document_parse", "file_read"],
  },

  category: "cognitive",
  source: "builtin",

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const action = (args["action"] as string) || "parse";
    const filePath = args["filePath"] as string;

    if (!filePath) {
      return JSON.stringify({
        success: false,
        error: { code: "MISSING_ARG", message: "filePath is required" },
      });
    }

    const SUPPORTED = [".pdf", ".docx", ".md", ".txt"];
    const ext = extname(filePath).toLowerCase();
    if (!SUPPORTED.includes(ext)) {
      return JSON.stringify({
        success: false,
        error: {
          code: "UNSUPPORTED_FORMAT",
          message: `Unsupported "${ext}". Supported: ${SUPPORTED.join(", ")}`,
        },
      });
    }

    let buf: Buffer;
    try {
      buf = await readFile(filePath);
    } catch {
      return JSON.stringify({
        success: false,
        error: {
          code: "FILE_NOT_FOUND",
          message: `Cannot read: ${filePath}`,
        },
      });
    }

    try {
      if (ext === ".pdf") {
        const pdfParse = (await import("pdf-parse")).default;
        const data = await pdfParse(buf);
        if (action === "metadata") {
          return JSON.stringify({
            success: true,
            data: {
              text: "",
              tables: [],
              metadata: { pages: data.numpages, info: data.info },
            },
          });
        }
        return JSON.stringify({
          success: true,
          data: {
            text: data.text,
            tables: [],
            metadata: { pages: data.numpages },
          },
        });
      }

      if (ext === ".docx") {
        const mammoth = await import("mammoth");
        const result = await mammoth.extractRawText({ buffer: buf });
        return JSON.stringify({
          success: true,
          data: { text: result.value, tables: [], metadata: {} },
        });
      }

      // .md and .txt
      const text = buf.toString("utf-8");
      return JSON.stringify({
        success: true,
        data: { text, tables: [], metadata: { size: buf.length } },
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return JSON.stringify({
        success: false,
        error: { code: "PARSE_ERROR", message: msg },
      });
    }
  },
};
