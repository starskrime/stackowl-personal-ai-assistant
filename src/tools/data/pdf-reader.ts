import type { ToolImplementation, ToolContext } from "../registry.js";

export const PDFReaderTool: ToolImplementation = {
  definition: {
    name: "pdf_reader",
    description:
      "Read and extract text from PDF files. Can get full text, specific pages, " +
      "metadata, page count, and search within PDFs.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description: "Action: read (default), info, search, extract_images",
        },
        path: {
          type: "string",
          description: "Path to the PDF file",
        },
        pages: {
          type: "string",
          description: "Page range: '1-5', '3', '1,3,5', 'all' (default: first 10 pages)",
        },
        query: {
          type: "string",
          description: "Search query for search action",
        },
      },
      required: ["path"],
    },
  },

  category: "filesystem",

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const action = (args.action as string) || "read";
    const filePath = String(args.path);
    const pages = args.pages as string | undefined;
    const query = args.query as string | undefined;
    const cwd = context.cwd || process.cwd();

    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const { resolve } = await import("node:path");
    const { existsSync } = await import("node:fs");
    const exec = promisify(execFile);

    const resolvedPath = resolve(cwd, filePath);
    if (!existsSync(resolvedPath)) {
      return `Error: File not found: ${resolvedPath}`;
    }

    const shell = async (cmd: string): Promise<string> => {
      const { stdout } = await exec("bash", ["-c", cmd], { timeout: 30000 });
      return stdout.trim();
    };

    try {
      switch (action) {
        case "read": {
          // Try multiple PDF extraction methods
          let text = "";

          // Method 1: macOS built-in textutil + mdimport
          try {
            if (pages) {
              // Parse page range for pdftotext
              const pageFlag = pages === "all" ? "" : `-f ${pages.split("-")[0] || pages.split(",")[0]} -l ${pages.split("-")[1] || pages.split(",").pop()}`;
              text = await shell(
                `pdftotext ${pageFlag} "${resolvedPath}" - 2>/dev/null`,
              );
            } else {
              text = await shell(
                `pdftotext -f 1 -l 10 "${resolvedPath}" - 2>/dev/null`,
              );
            }
            if (text.trim()) return `📄 PDF Content (${resolvedPath}):\n\n${text}`;
          } catch { /* try next method */ }

          // Method 2: Python with PyPDF2 or pdfplumber
          try {
            const pageRange = pages || "0:10";
            const pyScript = `
import sys
try:
    import pdfplumber
    with pdfplumber.open("${resolvedPath}") as pdf:
        pages_range = "${pageRange}"
        if pages_range == "all":
            target = range(len(pdf.pages))
        elif "-" in pages_range:
            s, e = pages_range.split("-")
            target = range(int(s)-1, min(int(e), len(pdf.pages)))
        elif "," in pages_range:
            target = [int(p)-1 for p in pages_range.split(",")]
        elif ":" in pages_range:
            s, e = pages_range.split(":")
            target = range(int(s), min(int(e), len(pdf.pages)))
        else:
            target = [int(pages_range)-1]
        for i in target:
            if 0 <= i < len(pdf.pages):
                text = pdf.pages[i].extract_text()
                if text:
                    print(f"--- Page {i+1} ---")
                    print(text)
except ImportError:
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader("${resolvedPath}")
        for i, page in enumerate(reader.pages[:10]):
            text = page.extract_text()
            if text:
                print(f"--- Page {i+1} ---")
                print(text)
    except ImportError:
        print("NO_LIB")
`;
            text = await shell(`python3 -c '${pyScript.replace(/'/g, "'\\''")}' 2>/dev/null`);
            if (text && text !== "NO_LIB") return `📄 PDF Content (${resolvedPath}):\n\n${text}`;
          } catch { /* try next */ }

          // Method 3: macOS mdimport spotlight extraction
          try {
            text = await shell(`mdimport -d2 "${resolvedPath}" 2>&1 | head -200`);
            if (text.trim()) return `📄 PDF Content (extracted via Spotlight):\n\n${text}`;
          } catch { /* fallthrough */ }

          return (
            `Could not extract text from PDF. Install one of:\n` +
            `  brew install poppler  (provides pdftotext)\n` +
            `  pip3 install pdfplumber\n` +
            `  pip3 install PyPDF2`
          );
        }

        case "info": {
          let info = "";
          try {
            info = await shell(`pdfinfo "${resolvedPath}" 2>/dev/null`);
          } catch {
            try {
              info = await shell(
                `python3 -c "
from PyPDF2 import PdfReader
r = PdfReader('${resolvedPath}')
meta = r.metadata
print(f'Pages: {len(r.pages)}')
if meta:
    for k,v in meta.items():
        print(f'{k}: {v}')
" 2>/dev/null`,
              );
            } catch {
              info = await shell(`mdls -name kMDItemNumberOfPages -name kMDItemTitle -name kMDItemAuthors "${resolvedPath}" 2>/dev/null`);
            }
          }
          return `📋 PDF Info (${resolvedPath}):\n${info || "Could not read metadata."}`;
        }

        case "search": {
          if (!query) return "Error: search requires 'query'.";
          try {
            const text = await shell(`pdftotext "${resolvedPath}" - 2>/dev/null`);
            const lines = text.split("\n");
            const matches = lines
              .map((line, i) => ({ line: i + 1, text: line }))
              .filter((l) => l.text.toLowerCase().includes(query.toLowerCase()));

            if (matches.length === 0) return `No matches for "${query}" in ${resolvedPath}.`;

            const formatted = matches
              .slice(0, 20)
              .map((m) => `  Line ${m.line}: ${m.text.trim()}`);
            return `🔍 Found ${matches.length} matches for "${query}":\n\n${formatted.join("\n")}`;
          } catch {
            return "Search requires pdftotext. Install: brew install poppler";
          }
        }

        default:
          return `Unknown action: "${action}". Available: read, info, search`;
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error: ${msg}`;
    }
  },
};
