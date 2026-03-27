import type { ToolImplementation } from "../registry.js";

function extractMeta(html: string, property: string): string | null {
  // Try og: property first
  const ogRe = new RegExp(
    `<meta[^>]+property=["']og:${property}["'][^>]+content=["']([^"']+)["']`,
    "i",
  );
  let match = ogRe.exec(html);
  if (match) return match[1];

  // Try reversed attribute order (content before property)
  const ogRevRe = new RegExp(
    `<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:${property}["']`,
    "i",
  );
  match = ogRevRe.exec(html);
  if (match) return match[1];

  return null;
}

function extractMetaName(html: string, name: string): string | null {
  const re = new RegExp(
    `<meta[^>]+name=["']${name}["'][^>]+content=["']([^"']+)["']`,
    "i",
  );
  let match = re.exec(html);
  if (match) return match[1];

  const revRe = new RegExp(
    `<meta[^>]+content=["']([^"']+)["'][^>]+name=["']${name}["']`,
    "i",
  );
  match = revRe.exec(html);
  if (match) return match[1];

  return null;
}

function extractTitle(html: string): string | null {
  const match = /<title[^>]*>([^<]+)<\/title>/i.exec(html);
  return match ? match[1].trim() : null;
}

export const LinkPreviewTool: ToolImplementation = {
  definition: {
    name: "link_preview",
    description:
      "Get a preview of a URL — title, description, and image. Like link previews in chat apps.",
    parameters: {
      type: "object",
      properties: {
        url: {
          type: "string",
          description: "The URL to preview.",
        },
      },
      required: ["url"],
    },
  },

  async execute(args, _context) {
    const url = args.url as string;

    try {
      const resp = await fetch(url, {
        signal: AbortSignal.timeout(15000),
        headers: {
          "User-Agent": "StackOwl LinkPreview/1.0",
          Accept: "text/html",
        },
      });

      if (!resp.ok) {
        return `Error: HTTP ${resp.status} ${resp.statusText} for ${url}`;
      }

      const html = await resp.text();

      const title =
        extractMeta(html, "title") ?? extractTitle(html) ?? "No title found";
      const description =
        extractMeta(html, "description") ??
        extractMetaName(html, "description") ??
        "No description found";
      const image = extractMeta(html, "image") ?? "No image found";
      const type = extractMeta(html, "type") ?? "website";
      const siteName = extractMeta(html, "site_name") ?? "";

      const lines = [
        `Title: ${title}`,
        `Description: ${description}`,
        `Image: ${image}`,
        `Type: ${type}`,
      ];
      if (siteName) lines.push(`Site: ${siteName}`);
      lines.push(`URL: ${url}`);

      return lines.join("\n");
    } catch (e) {
      return `link_preview error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
