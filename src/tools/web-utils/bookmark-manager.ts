import type { ToolImplementation, ToolContext } from "../registry.js";
import { resolve, join } from "node:path";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";

interface Bookmark {
  url: string;
  title: string;
  tags: string[];
  note?: string;
  createdAt: string;
}

interface BookmarkStore {
  bookmarks: Bookmark[];
}

function getStorePath(cwd: string): string {
  const dir = resolve(cwd, "workspace");
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  return join(dir, "bookmarks.json");
}

function loadStore(cwd: string): BookmarkStore {
  const path = getStorePath(cwd);
  if (!existsSync(path)) return { bookmarks: [] };
  return JSON.parse(readFileSync(path, "utf-8"));
}

function saveStore(cwd: string, store: BookmarkStore): void {
  writeFileSync(getStorePath(cwd), JSON.stringify(store, null, 2));
}

export const BookmarkManagerTool: ToolImplementation = {
  definition: {
    name: "bookmarks",
    description:
      "Save, search, list, and manage web bookmarks with tags and notes. " +
      "Persistent bookmark storage for URLs the user wants to remember.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description: "Action: save, list, search, delete, tags, export",
        },
        url: {
          type: "string",
          description: "URL to bookmark (for save action)",
        },
        title: {
          type: "string",
          description: "Title for the bookmark (for save action)",
        },
        tags: {
          type: "string",
          description: "Comma-separated tags (for save or search)",
        },
        note: {
          type: "string",
          description: "Optional note about the bookmark",
        },
        query: {
          type: "string",
          description: "Search query (searches title, URL, tags, notes)",
        },
      },
      required: ["action"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const action = String(args.action);
    const cwd = context.cwd || process.cwd();

    try {
      switch (action) {
        case "save": {
          const url = args.url as string;
          if (!url) return "Error: save requires 'url'.";
          const title = (args.title as string) || url;
          const tags = args.tags
            ? String(args.tags).split(",").map((t) => t.trim().toLowerCase())
            : [];
          const note = args.note as string | undefined;

          const store = loadStore(cwd);

          // Check for duplicate
          const existing = store.bookmarks.findIndex((b) => b.url === url);
          if (existing >= 0) {
            // Update existing
            store.bookmarks[existing] = {
              ...store.bookmarks[existing],
              title,
              tags: [...new Set([...store.bookmarks[existing].tags, ...tags])],
              note: note || store.bookmarks[existing].note,
            };
            saveStore(cwd, store);
            return `🔖 Updated bookmark: ${title}\n  URL: ${url}\n  Tags: ${store.bookmarks[existing].tags.join(", ")}`;
          }

          store.bookmarks.push({
            url,
            title,
            tags,
            note,
            createdAt: new Date().toISOString(),
          });
          saveStore(cwd, store);
          return (
            `🔖 Bookmarked: ${title}\n` +
            `  URL: ${url}\n` +
            (tags.length ? `  Tags: ${tags.join(", ")}\n` : "") +
            (note ? `  Note: ${note}` : "")
          );
        }

        case "list": {
          const store = loadStore(cwd);
          const tagFilter = args.tags
            ? String(args.tags).split(",").map((t) => t.trim().toLowerCase())
            : [];

          let bookmarks = store.bookmarks;
          if (tagFilter.length > 0) {
            bookmarks = bookmarks.filter((b) =>
              tagFilter.some((t) => b.tags.includes(t)),
            );
          }

          if (bookmarks.length === 0)
            return tagFilter.length
              ? `No bookmarks found with tags: ${tagFilter.join(", ")}`
              : "No bookmarks saved yet.";

          const formatted = bookmarks
            .slice(-20) // Show last 20
            .map(
              (b, i) =>
                `${i + 1}. **${b.title}**\n` +
                `   ${b.url}\n` +
                (b.tags.length ? `   Tags: ${b.tags.join(", ")}` : "") +
                (b.note ? `\n   Note: ${b.note}` : ""),
            );

          return `📚 Bookmarks (${bookmarks.length} total):\n\n${formatted.join("\n\n")}`;
        }

        case "search": {
          const query = (args.query as string || "").toLowerCase();
          if (!query) return "Error: search requires 'query'.";

          const store = loadStore(cwd);
          const matches = store.bookmarks.filter(
            (b) =>
              b.title.toLowerCase().includes(query) ||
              b.url.toLowerCase().includes(query) ||
              b.tags.some((t) => t.includes(query)) ||
              (b.note && b.note.toLowerCase().includes(query)),
          );

          if (matches.length === 0) return `No bookmarks matching "${query}".`;

          const formatted = matches.map(
            (b, i) =>
              `${i + 1}. **${b.title}**\n   ${b.url}\n   Tags: ${b.tags.join(", ") || "none"}`,
          );
          return `🔍 Found ${matches.length} bookmark(s):\n\n${formatted.join("\n\n")}`;
        }

        case "delete": {
          const url = args.url as string;
          const query = args.query as string;
          if (!url && !query)
            return "Error: delete requires 'url' or 'query'.";

          const store = loadStore(cwd);
          const before = store.bookmarks.length;

          if (url) {
            store.bookmarks = store.bookmarks.filter((b) => b.url !== url);
          } else if (query) {
            const q = query.toLowerCase();
            store.bookmarks = store.bookmarks.filter(
              (b) =>
                !b.title.toLowerCase().includes(q) &&
                !b.url.toLowerCase().includes(q),
            );
          }

          const removed = before - store.bookmarks.length;
          if (removed === 0) return "No matching bookmarks found to delete.";
          saveStore(cwd, store);
          return `🗑️ Deleted ${removed} bookmark(s). ${store.bookmarks.length} remaining.`;
        }

        case "tags": {
          const store = loadStore(cwd);
          const tagCounts = new Map<string, number>();
          for (const b of store.bookmarks) {
            for (const t of b.tags) {
              tagCounts.set(t, (tagCounts.get(t) || 0) + 1);
            }
          }

          if (tagCounts.size === 0) return "No tags found.";

          const sorted = [...tagCounts.entries()].sort((a, b) => b[1] - a[1]);
          const formatted = sorted.map(([tag, count]) => `  ${tag} (${count})`);
          return `🏷️ All tags:\n${formatted.join("\n")}`;
        }

        case "export": {
          const store = loadStore(cwd);
          const html = [
            "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
            "<META HTTP-EQUIV=\"Content-Type\" CONTENT=\"text/html; charset=UTF-8\">",
            "<TITLE>Bookmarks</TITLE>",
            "<H1>Bookmarks</H1>",
            "<DL><p>",
            ...store.bookmarks.map(
              (b) =>
                `  <DT><A HREF="${b.url}" ADD_DATE="${Math.floor(new Date(b.createdAt).getTime() / 1000)}" TAGS="${b.tags.join(",")}">${b.title}</A>`,
            ),
            "</DL><p>",
          ].join("\n");

          const outPath = resolve(cwd, "workspace", "bookmarks_export.html");
          writeFileSync(outPath, html);
          return `📤 Exported ${store.bookmarks.length} bookmarks to: ${outPath}\nThis file can be imported into any browser.`;
        }

        default:
          return (
            `Unknown action: "${action}". Available:\n` +
            `  save — Save a bookmark (requires url)\n` +
            `  list — List bookmarks (optional tags filter)\n` +
            `  search — Search bookmarks (requires query)\n` +
            `  delete — Delete bookmarks (requires url or query)\n` +
            `  tags — List all tags with counts\n` +
            `  export — Export as browser-importable HTML`
          );
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error (${action}): ${msg}`;
    }
  },
};
