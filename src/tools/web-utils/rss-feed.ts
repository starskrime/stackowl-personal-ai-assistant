import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import type { ToolImplementation } from "../registry.js";

interface FeedSubscription {
  name: string;
  url: string;
  addedAt: string;
}

interface FeedsFile {
  feeds: FeedSubscription[];
}

interface FeedItem {
  title: string;
  link: string;
  date: string;
  description: string;
}

async function loadFeeds(filePath: string): Promise<FeedsFile> {
  try {
    const raw = await readFile(filePath, "utf-8");
    return JSON.parse(raw) as FeedsFile;
  } catch {
    return { feeds: [] };
  }
}

async function saveFeeds(filePath: string, data: FeedsFile): Promise<void> {
  const dir = filePath.substring(0, filePath.lastIndexOf("/"));
  await mkdir(dir, { recursive: true });
  await writeFile(filePath, JSON.stringify(data, null, 2), "utf-8");
}

function parseItems(xml: string): FeedItem[] {
  const items: FeedItem[] = [];

  // Try RSS <item> elements
  const itemRegex = /<item[\s>]([\s\S]*?)<\/item>/gi;
  let match: RegExpExecArray | null;

  while ((match = itemRegex.exec(xml)) !== null) {
    const block = match[1];
    items.push({
      title: extractTag(block, "title"),
      link: extractTag(block, "link"),
      date: extractTag(block, "pubDate") || extractTag(block, "dc:date"),
      description: extractTag(block, "description"),
    });
  }

  // If no RSS items found, try Atom <entry> elements
  if (items.length === 0) {
    const entryRegex = /<entry[\s>]([\s\S]*?)<\/entry>/gi;
    while ((match = entryRegex.exec(xml)) !== null) {
      const block = match[1];
      const linkMatch = /<link[^>]+href=["']([^"']+)["']/i.exec(block);
      items.push({
        title: extractTag(block, "title"),
        link: linkMatch ? linkMatch[1] : extractTag(block, "link"),
        date: extractTag(block, "updated") || extractTag(block, "published"),
        description:
          extractTag(block, "summary") || extractTag(block, "content"),
      });
    }
  }

  return items;
}

function extractTag(xml: string, tag: string): string {
  // Handle CDATA sections
  const cdataRe = new RegExp(
    `<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>`,
    "i",
  );
  let match = cdataRe.exec(xml);
  if (match) return match[1].trim();

  const re = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, "i");
  match = re.exec(xml);
  if (match) return match[1].trim();

  return "";
}

function stripHtml(str: string): string {
  return str
    .replace(/<[^>]+>/g, "")
    .replace(/&[a-z]+;/gi, " ")
    .trim();
}

function formatItems(items: FeedItem[], max: number): string {
  const limited = items.slice(0, max);
  if (limited.length === 0) return "No items found in feed.";

  return limited
    .map((item, i) => {
      const desc = stripHtml(item.description).slice(0, 200);
      const parts = [`${i + 1}. ${item.title || "(untitled)"}`];
      if (item.link) parts.push(`   Link: ${item.link}`);
      if (item.date) parts.push(`   Date: ${item.date}`);
      if (desc) parts.push(`   ${desc}`);
      return parts.join("\n");
    })
    .join("\n\n");
}

export const RSSFeedTool: ToolImplementation = {
  definition: {
    name: "rss_feed",
    description:
      "Read RSS/Atom feeds — get latest articles from any feed URL. Subscribe to feeds for regular checking.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["read", "subscribe", "list"],
          description:
            'Action: "read" a feed URL, "subscribe" to save a feed, or "list" subscribed feeds.',
        },
        url: {
          type: "string",
          description: 'Feed URL. Required for "read" and "subscribe".',
        },
        name: {
          type: "string",
          description:
            'Friendly name for the subscription. Used with "subscribe".',
        },
      },
      required: ["action"],
    },
  },

  async execute(args, context) {
    const action = args.action as string;
    const url = args.url as string | undefined;
    const name = args.name as string | undefined;
    const feedsPath = join(context.cwd, "workspace", "feeds.json");

    try {
      if (action === "read") {
        if (!url) return "Error: url is required for the read action.";

        const resp = await fetch(url, {
          signal: AbortSignal.timeout(15000),
          headers: {
            "User-Agent": "StackOwl RSSFeed/1.0",
            Accept:
              "application/rss+xml, application/atom+xml, application/xml, text/xml",
          },
        });
        if (!resp.ok) return `Error: HTTP ${resp.status} ${resp.statusText}`;

        const xml = await resp.text();
        const items = parseItems(xml);
        const feedTitle = extractTag(xml, "title");

        return `Feed: ${feedTitle || url}\nItems (latest 10):\n\n${formatItems(items, 10)}`;
      }

      if (action === "subscribe") {
        if (!url) return "Error: url is required for the subscribe action.";
        const feedName = name || url;

        const data = await loadFeeds(feedsPath);
        if (data.feeds.some((f) => f.url === url)) {
          return `Already subscribed to ${url}.`;
        }

        data.feeds.push({
          name: feedName,
          url,
          addedAt: new Date().toISOString(),
        });
        await saveFeeds(feedsPath, data);
        return `Subscribed to "${feedName}" (${url}).`;
      }

      if (action === "list") {
        const data = await loadFeeds(feedsPath);
        if (data.feeds.length === 0) return "No feed subscriptions yet.";
        const lines = data.feeds.map(
          (f) => `- ${f.name} — ${f.url} (added ${f.addedAt})`,
        );
        return `Subscribed feeds:\n${lines.join("\n")}`;
      }

      return `Unknown action: ${action}. Use read, subscribe, or list.`;
    } catch (e) {
      return `rss_feed error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
