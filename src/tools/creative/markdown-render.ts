/**
 * StackOwl — Markdown Render Tool
 *
 * Renders markdown content to HTML using a simple built-in converter.
 * Saves both .md and .html versions.
 */

import { mkdir, writeFile } from "node:fs/promises";
import { resolve, basename } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";

function markdownToHtml(md: string): string {
  let html = md;

  // Code blocks (fenced) — must come before inline replacements
  html = html.replace(
    /```(\w*)\n([\s\S]*?)```/g,
    (_match, lang, code) =>
      `<pre><code class="language-${lang}">${code
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")}</code></pre>`,
  );

  // Inline code
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

  // Headers
  html = html.replace(/^######\s+(.+)$/gm, "<h6>$1</h6>");
  html = html.replace(/^#####\s+(.+)$/gm, "<h5>$1</h5>");
  html = html.replace(/^####\s+(.+)$/gm, "<h4>$1</h4>");
  html = html.replace(/^###\s+(.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^##\s+(.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^#\s+(.+)$/gm, "<h1>$1</h1>");

  // Bold and italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');

  // Images
  html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" />');

  // Unordered lists
  html = html.replace(/^[-*]\s+(.+)$/gm, "<li>$1</li>");
  html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>\n$1</ul>\n");

  // Ordered lists
  html = html.replace(/^\d+\.\s+(.+)$/gm, "<li>$1</li>");

  // Horizontal rules
  html = html.replace(/^---+$/gm, "<hr />");

  // Blockquotes
  html = html.replace(/^>\s+(.+)$/gm, "<blockquote>$1</blockquote>");

  // Paragraphs: wrap remaining standalone lines
  html = html.replace(/^(?!<[a-z])((?!<\/)[^\n]+)$/gm, "<p>$1</p>");

  const fullHtml = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Rendered Markdown</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #333; }
    pre { background: #f6f8fa; padding: 1rem; border-radius: 6px; overflow-x: auto; }
    code { background: #f0f0f0; padding: 0.2em 0.4em; border-radius: 3px; font-size: 0.9em; }
    pre code { background: none; padding: 0; }
    blockquote { border-left: 4px solid #ddd; margin: 0; padding-left: 1rem; color: #666; }
    img { max-width: 100%; }
    hr { border: none; border-top: 1px solid #ddd; margin: 2rem 0; }
  </style>
</head>
<body>
${html}
</body>
</html>`;

  return fullHtml;
}

export const MarkdownRenderTool: ToolImplementation = {
  definition: {
    name: "markdown_render",
    description:
      "Render markdown content to HTML file. Saves both markdown and HTML versions.",
    parameters: {
      type: "object",
      properties: {
        content: {
          type: "string",
          description: "Markdown content string to render.",
        },
        output: {
          type: "string",
          description: "Output filename (without extension).",
        },
      },
      required: ["content", "output"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const content = args["content"] as string;
      const output = args["output"] as string;
      if (!content) return "Error: 'content' parameter is required.";
      if (!output) return "Error: 'output' parameter is required.";

      const safeName = basename(output).replace(/[^a-zA-Z0-9_-]/g, "_");

      const renderedDir = resolve(_context.cwd, "workspace", "rendered");
      await mkdir(renderedDir, { recursive: true });

      const mdPath = resolve(renderedDir, `${safeName}.md`);
      const htmlPath = resolve(renderedDir, `${safeName}.html`);

      await writeFile(mdPath, content, "utf-8");

      const html = markdownToHtml(content);
      await writeFile(htmlPath, html, "utf-8");

      return `Files saved:\n- Markdown: ${mdPath}\n- HTML: ${htmlPath}`;
    } catch (error: any) {
      return `Error rendering markdown: ${error.message ?? String(error)}`;
    }
  },
};
