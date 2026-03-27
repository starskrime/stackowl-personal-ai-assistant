import type { ToolImplementation, ToolContext } from "../registry.js";

export const YouTubeSearchTool: ToolImplementation = {
  definition: {
    name: "youtube_search",
    description:
      "Search YouTube for videos. Returns video titles, channels, URLs, and descriptions. " +
      "Can also get video details and transcript/captions if available.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description: "Action: search (default), trending, transcript",
        },
        query: {
          type: "string",
          description: "Search query for finding videos",
        },
        url: {
          type: "string",
          description: "YouTube video URL for transcript action",
        },
        limit: {
          type: "number",
          description: "Number of results to return (default: 5, max: 20)",
        },
      },
      required: ["query"],
    },
  },

  category: "network",

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const action = (args.action as string) || "search";
    const query = String(args.query || "");
    const url = args.url as string | undefined;
    const limit = Math.min((args.limit as number) || 5, 20);

    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const exec = promisify(execFile);

    const shell = async (cmd: string): Promise<string> => {
      const { stdout } = await exec("bash", ["-c", cmd], { timeout: 30000 });
      return stdout.trim();
    };

    try {
      if (action === "transcript" && url) {
        // Try yt-dlp for subtitles
        try {
          const result = await shell(
            `yt-dlp --skip-download --write-auto-sub --sub-lang en --sub-format txt --print-to-file subtitle "${url}" - 2>/dev/null || ` +
              `yt-dlp --skip-download --write-sub --sub-lang en -o - "${url}" 2>/dev/null | head -200`,
          );
          if (result) return `📝 Transcript for ${url}:\n\n${result}`;
        } catch {
          /* fallthrough */
        }

        // Fallback: Python youtube_transcript_api
        try {
          const videoId = url.match(
            /(?:v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/,
          )?.[1];
          if (videoId) {
            const py = await shell(
              `python3 -c "
from youtube_transcript_api import YouTubeTranscriptApi
transcript = YouTubeTranscriptApi.get_transcript('${videoId}')
for entry in transcript[:100]:
    print(f'[{int(entry[\"start\"])//60}:{int(entry[\"start\"])%60:02d}] {entry[\"text\"]}')
" 2>/dev/null`,
            );
            if (py) return `📝 Transcript for ${url}:\n\n${py}`;
          }
        } catch {
          /* fallthrough */
        }

        return "Could not fetch transcript. Install: pip3 install youtube_transcript_api or brew install yt-dlp";
      }

      // Search using yt-dlp (most reliable, no API key needed)
      try {
        const escaped = query.replace(/"/g, '\\"');
        const result = await shell(
          `yt-dlp "ytsearch${limit}:${escaped}" --flat-playlist --print "%(title)s|||%(channel)s|||%(url)s|||%(duration_string)s|||%(view_count)s" 2>/dev/null | head -${limit}`,
        );

        if (result) {
          const lines = result.split("\n").filter(Boolean);
          const formatted = lines.map((line, i) => {
            const [title, channel, videoUrl, duration, views] =
              line.split("|||");
            const viewCount = views ? Number(views).toLocaleString() : "N/A";
            return (
              `${i + 1}. **${title || "Unknown"}**\n` +
              `   Channel: ${channel || "Unknown"} | Duration: ${duration || "N/A"} | Views: ${viewCount}\n` +
              `   ${videoUrl || ""}`
            );
          });
          return `🎬 YouTube results for "${query}":\n\n${formatted.join("\n\n")}`;
        }
      } catch {
        /* fallthrough to Python */
      }

      // Fallback: scrape YouTube search page
      try {
        const escaped = query.replace(/'/g, "\\'");
        const pyResult = await shell(
          `python3 -c "
import urllib.request, urllib.parse, json, re
query = urllib.parse.quote('${escaped}')
url = f'https://www.youtube.com/results?search_query={query}'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
resp = urllib.request.urlopen(req, timeout=10)
html = resp.read().decode()
# Extract video data from ytInitialData
match = re.search(r'var ytInitialData = ({.*?});', html)
if match:
    data = json.loads(match.group(1))
    contents = data.get('contents',{}).get('twoColumnSearchResultsRenderer',{}).get('primaryContents',{}).get('sectionListRenderer',{}).get('contents',[{}])[0].get('itemSectionRenderer',{}).get('contents',[])
    count = 0
    for item in contents:
        vid = item.get('videoRenderer',{})
        if not vid: continue
        title = vid.get('title',{}).get('runs',[{}])[0].get('text','')
        vid_id = vid.get('videoId','')
        channel = vid.get('ownerText',{}).get('runs',[{}])[0].get('text','')
        length = vid.get('lengthText',{}).get('simpleText','')
        views = vid.get('viewCountText',{}).get('simpleText','')
        print(f'{title}|||{channel}|||https://youtube.com/watch?v={vid_id}|||{length}|||{views}')
        count += 1
        if count >= ${limit}: break
" 2>/dev/null`,
        );

        if (pyResult) {
          const lines = pyResult.split("\n").filter(Boolean);
          const formatted = lines.map((line, i) => {
            const [title, channel, videoUrl, duration, views] =
              line.split("|||");
            return (
              `${i + 1}. **${title}**\n` +
              `   Channel: ${channel} | Duration: ${duration} | ${views}\n` +
              `   ${videoUrl}`
            );
          });
          return `🎬 YouTube results for "${query}":\n\n${formatted.join("\n\n")}`;
        }
      } catch {
        /* fallthrough */
      }

      return (
        `Could not search YouTube. For best results, install yt-dlp:\n` +
        `  brew install yt-dlp`
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error: ${msg}`;
    }
  },
};
