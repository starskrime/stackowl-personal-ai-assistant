import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";

export const MusicControlTool: ToolImplementation = {
  definition: {
    name: "music_control",
    description:
      "Control music playback on macOS — Apple Music and Spotify. " +
      "Play, pause, skip, search, get current track, adjust volume, toggle shuffle/repeat.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description:
            "Action: play, pause, toggle, next, previous, " +
            "now_playing, search_play, set_volume, shuffle, repeat, " +
            "add_to_library, love, queue",
        },
        query: {
          type: "string",
          description:
            "Search query for search_play or queue (song name, artist, album)",
        },
        app: {
          type: "string",
          description: "Music app: 'music' (Apple Music, default) or 'spotify'",
        },
        value: {
          type: "number",
          description: "Volume level 0-100 for set_volume",
        },
      },
      required: ["action"],
    },
  },

  category: "system",

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const action = String(args.action);
    const query = args.query as string | undefined;
    const appChoice = ((args.app as string) || "music").toLowerCase();
    const value = args.value as number | undefined;

    log.tool.debug("music_control.execute: entry", { action, appChoice, query });

    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const exec = promisify(execFile);

    const appName = appChoice === "spotify" ? "Spotify" : "Music";
    log.tool.debug("music_control.execute: app selected", { appName });

    const osa = async (script: string): Promise<string> => {
      const { stdout } = await exec("osascript", ["-e", script], {
        timeout: 10000,
      });
      return stdout.trim();
    };

    try {
      switch (action) {
        case "play": {
          log.tool.debug("music_control.execute: sending play command via AppleScript", { appName });
          await osa(`tell application "${appName}" to play`);
          const result = `▶️ Playing (${appName})`;
          log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "pause": {
          log.tool.debug("music_control.execute: sending pause command via AppleScript", { appName });
          await osa(`tell application "${appName}" to pause`);
          const result = `⏸ Paused (${appName})`;
          log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "toggle": {
          log.tool.debug("music_control.execute: sending playpause toggle via AppleScript", { appName });
          await osa(`tell application "${appName}" to playpause`);
          const result = `⏯ Toggled play/pause (${appName})`;
          log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "next": {
          log.tool.debug("music_control.execute: skipping to next track via AppleScript", { appName });
          await osa(`tell application "${appName}" to next track`);
          const result = `⏭ Skipped to next track (${appName})`;
          log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "previous": {
          log.tool.debug("music_control.execute: going to previous track via AppleScript", { appName });
          await osa(`tell application "${appName}" to previous track`);
          const result = `⏮ Previous track (${appName})`;
          log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "now_playing": {
          log.tool.debug("music_control.execute: querying now playing via AppleScript", { appName });
          if (appChoice === "spotify") {
            const name = await osa(
              'tell application "Spotify" to name of current track',
            );
            const artist = await osa(
              'tell application "Spotify" to artist of current track',
            );
            const album = await osa(
              'tell application "Spotify" to album of current track',
            );
            const pos = await osa(
              'tell application "Spotify" to player position',
            );
            const dur = await osa(
              'tell application "Spotify" to (duration of current track) / 1000',
            );
            const state = await osa(
              'tell application "Spotify" to player state as string',
            );
            const result =
              `🎵 Now Playing (Spotify):\n` +
              `  Track: ${name}\n` +
              `  Artist: ${artist}\n` +
              `  Album: ${album}\n` +
              `  Position: ${Math.round(Number(pos))}s / ${Math.round(Number(dur))}s\n` +
              `  State: ${state}`;
            log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
            return result;
          }
          const name = await osa(
            'tell application "Music" to name of current track',
          );
          const artist = await osa(
            'tell application "Music" to artist of current track',
          );
          const album = await osa(
            'tell application "Music" to album of current track',
          );
          const pos = await osa('tell application "Music" to player position');
          const dur = await osa(
            'tell application "Music" to duration of current track',
          );
          const state = await osa(
            'tell application "Music" to player state as string',
          );
          const result =
            `🎵 Now Playing (Apple Music):\n` +
            `  Track: ${name}\n` +
            `  Artist: ${artist}\n` +
            `  Album: ${album}\n` +
            `  Position: ${Math.round(Number(pos))}s / ${Math.round(Number(dur))}s\n` +
            `  State: ${state}`;
          log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "search_play": {
          if (!query) return "Error: search_play requires a 'query' parameter.";
          const escaped = query.replace(/"/g, '\\"');
          log.tool.debug("music_control.execute: searching and playing via AppleScript", { appName, query });
          if (appChoice === "spotify") {
            // Spotify doesn't support AppleScript search well — open search URI
            const uri = `spotify:search:${encodeURIComponent(query)}`;
            await osa(`open location "${uri}"`);
            const result = `🔍 Opened Spotify search for: "${query}". Use now_playing after a moment to see what's playing.`;
            log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
            return result;
          }
          // Apple Music search and play
          const script = `
tell application "Music"
  set searchResults to (search playlist "Library" for "${escaped}")
  if (count of searchResults) > 0 then
    play item 1 of searchResults
    return "Playing: " & name of item 1 of searchResults & " by " & artist of item 1 of searchResults
  else
    return "No results found for: ${escaped}"
  end if
end tell`;
          const searchResult = await osa(script);
          const result = `🔍 ${searchResult}`;
          log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "set_volume": {
          if (value === undefined || value < 0 || value > 100)
            return "Error: set_volume requires value 0-100.";
          log.tool.debug("music_control.execute: setting volume via AppleScript", { appName, value });
          await osa(
            `tell application "${appName}" to set sound volume to ${value}`,
          );
          const result = `🔊 ${appName} volume set to ${value}%`;
          log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "shuffle": {
          log.tool.debug("music_control.execute: toggling shuffle via AppleScript", { appName });
          if (appChoice === "spotify") {
            const current = await osa(
              'tell application "Spotify" to shuffling',
            );
            const newVal = current === "true" ? "false" : "true";
            await osa(
              `tell application "Spotify" to set shuffling to ${newVal}`,
            );
            const result = `🔀 Shuffle ${newVal === "true" ? "ON" : "OFF"} (Spotify)`;
            log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
            return result;
          }
          const current = await osa(
            'tell application "Music" to shuffle enabled',
          );
          const newVal = current === "true" ? "false" : "true";
          await osa(
            `tell application "Music" to set shuffle enabled to ${newVal}`,
          );
          const result = `🔀 Shuffle ${newVal === "true" ? "ON" : "OFF"} (Apple Music)`;
          log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "repeat": {
          log.tool.debug("music_control.execute: toggling repeat via AppleScript", { appName });
          if (appChoice === "spotify") {
            const current = await osa(
              'tell application "Spotify" to repeating',
            );
            const newVal = current === "true" ? "false" : "true";
            await osa(
              `tell application "Spotify" to set repeating to ${newVal}`,
            );
            const result = `🔁 Repeat ${newVal === "true" ? "ON" : "OFF"} (Spotify)`;
            log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
            return result;
          }
          // Apple Music cycles: off → all → one
          const current = await osa(
            'tell application "Music" to song repeat as string',
          );
          let newMode = "off";
          if (current === "off") newMode = "all";
          else if (current === "all") newMode = "one";
          await osa(
            `tell application "Music" to set song repeat to ${newMode}`,
          );
          const result = `🔁 Repeat: ${newMode} (Apple Music)`;
          log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "love": {
          log.tool.debug("music_control.execute: loving current track via AppleScript", { appName });
          if (appChoice !== "spotify") {
            await osa(
              'tell application "Music" to set loved of current track to true',
            );
            const result = `❤️ Loved current track (Apple Music)`;
            log.tool.debug("music_control.execute: exit", { success: true, action, resultLen: result.length });
            return result;
          }
          return "Love/save is not available via Spotify AppleScript.";
        }

        default:
          return (
            `Unknown action: "${action}". Available:\n` +
            `  Playback: play, pause, toggle, next, previous\n` +
            `  Info: now_playing\n` +
            `  Search: search_play (requires query)\n` +
            `  Settings: set_volume, shuffle, repeat\n` +
            `  Library: love`
          );
      }
    } catch (error) {
      log.tool.error("music_control.execute: failed", error instanceof Error ? error : new Error(String(error)), { action, appName });
      const msg = error instanceof Error ? error.message : String(error);
      if (
        msg.includes("not running") ||
        msg.includes("Connection is invalid")
      ) {
        return `${appName} is not running. Open it first, then try again.`;
      }
      return `Error (${action}): ${msg}`;
    }
  },
};
