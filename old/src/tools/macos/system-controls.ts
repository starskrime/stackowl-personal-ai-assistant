import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";

export const SystemControlsTool: ToolImplementation = {
  definition: {
    name: "system_controls",
    description:
      "Control macOS system settings: volume, brightness, dark mode, screen saver, lock screen, " +
      "Wi-Fi, Bluetooth, Do Not Disturb, screen resolution, and night shift.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description:
            "Action to perform: get_volume, set_volume, mute, unmute, " +
            "get_brightness, set_brightness, " +
            "dark_mode_on, dark_mode_off, get_dark_mode, " +
            "wifi_on, wifi_off, wifi_status, wifi_network, " +
            "bluetooth_on, bluetooth_off, bluetooth_status, " +
            "lock_screen, start_screensaver, sleep, restart, shutdown, " +
            "dnd_on, dnd_off, " +
            "get_battery, empty_trash",
        },
        value: {
          type: "number",
          description: "Value for set_volume (0-100) or set_brightness (0-100)",
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
    const value = args.value as number | undefined;
    log.tool.debug("system_controls.execute: entry", { action, value });

    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const exec = promisify(execFile);

    const osa = async (script: string): Promise<string> => {
      const { stdout } = await exec("osascript", ["-e", script], {
        timeout: 10000,
      });
      return stdout.trim();
    };

    const shell = async (cmd: string): Promise<string> => {
      const { stdout } = await exec("bash", ["-c", cmd], { timeout: 10000 });
      return stdout.trim();
    };

    try {
      switch (action) {
        // ── Volume ──
        case "get_volume": {
          log.tool.debug("system_controls.execute: querying volume via AppleScript");
          const vol = await osa("output volume of (get volume settings)");
          const muted = await osa("output muted of (get volume settings)");
          const result = `Volume: ${vol}%${muted === "true" ? " (muted)" : ""}`;
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "set_volume": {
          if (value === undefined || value < 0 || value > 100)
            return "Error: set_volume requires value 0-100.";
          log.tool.debug("system_controls.execute: setting volume via AppleScript", { value });
          await osa(`set volume output volume ${value}`);
          const result = `Volume set to ${value}%`;
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "mute": {
          log.tool.debug("system_controls.execute: muting audio via AppleScript");
          await osa("set volume output muted true");
          const result = "Audio muted.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "unmute": {
          log.tool.debug("system_controls.execute: unmuting audio via AppleScript");
          await osa("set volume output muted false");
          const result = "Audio unmuted.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        // ── Brightness ──
        case "get_brightness": {
          log.tool.debug("system_controls.execute: querying brightness via shell");
          const br = await shell(
            "brightness -l 2>/dev/null | grep brightness | head -1 | awk '{print $NF}' || echo 'unknown'",
          );
          if (br === "unknown") {
            // Fallback to CoreBrightness via Python
            try {
              const py = await shell(
                `python3 -c "import subprocess; r=subprocess.run(['brightness','-l'],capture_output=True,text=True); print(r.stdout)" 2>/dev/null`,
              );
              return `Brightness: ${py || "Could not read (install: brew install brightness)"}`;
            } catch (err) {
              log.tool.warn("system-controls: brightness read fallback failed", err);
              return "Brightness: Could not read. Install: brew install brightness";
            }
          }
          const result = `Brightness: ${Math.round(parseFloat(br) * 100)}%`;
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "set_brightness": {
          if (value === undefined || value < 0 || value > 100)
            return "Error: set_brightness requires value 0-100.";
          const brVal = (value / 100).toFixed(2);
          log.tool.debug("system_controls.execute: setting brightness via shell", { value, brVal });
          try {
            await shell(`brightness ${brVal}`);
            const result = `Brightness set to ${value}%`;
            log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
            return result;
          } catch (err) {
            log.tool.warn("system-controls: set brightness failed", err);
            return `Error: Could not set brightness. Install: brew install brightness`;
          }
        }

        // ── Dark Mode ──
        case "dark_mode_on": {
          log.tool.debug("system_controls.execute: enabling dark mode via AppleScript");
          await osa(
            'tell application "System Events" to tell appearance preferences to set dark mode to true',
          );
          const result = "Dark mode enabled.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "dark_mode_off": {
          log.tool.debug("system_controls.execute: disabling dark mode via AppleScript");
          await osa(
            'tell application "System Events" to tell appearance preferences to set dark mode to false',
          );
          const result = "Dark mode disabled.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "get_dark_mode": {
          log.tool.debug("system_controls.execute: querying dark mode state via AppleScript");
          const dm = await osa(
            'tell application "System Events" to tell appearance preferences to get dark mode',
          );
          const result = `Dark mode: ${dm === "true" ? "ON" : "OFF"}`;
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        // ── Wi-Fi ──
        case "wifi_on": {
          log.tool.debug("system_controls.execute: enabling Wi-Fi via networksetup");
          await shell("networksetup -setairportpower en0 on");
          const result = "Wi-Fi turned on.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "wifi_off": {
          log.tool.debug("system_controls.execute: disabling Wi-Fi via networksetup");
          await shell("networksetup -setairportpower en0 off");
          const result = "Wi-Fi turned off.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "wifi_status": {
          log.tool.debug("system_controls.execute: querying Wi-Fi status via networksetup");
          const status = await shell("networksetup -getairportpower en0");
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: status.length });
          return status;
        }
        case "wifi_network": {
          log.tool.debug("system_controls.execute: querying connected Wi-Fi network");
          try {
            const network = await shell(
              "networksetup -getairportnetwork en0 2>/dev/null || " +
                "ipconfig getifaddr en0 2>/dev/null || echo 'Not connected'",
            );
            const result = `Wi-Fi: ${network}`;
            log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
            return result;
          } catch (err) {
            log.tool.warn("system-controls: wifi network query failed", err);
            return "Wi-Fi: Not connected or could not detect.";
          }
        }

        // ── Bluetooth ──
        case "bluetooth_on": {
          log.tool.debug("system_controls.execute: enabling Bluetooth via blueutil");
          await shell(
            "blueutil --power 1 2>/dev/null || echo 'Install: brew install blueutil'",
          );
          const result = "Bluetooth turned on.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "bluetooth_off": {
          log.tool.debug("system_controls.execute: disabling Bluetooth via blueutil");
          await shell(
            "blueutil --power 0 2>/dev/null || echo 'Install: brew install blueutil'",
          );
          const result = "Bluetooth turned off.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "bluetooth_status": {
          log.tool.debug("system_controls.execute: querying Bluetooth status via blueutil");
          try {
            const bt = await shell("blueutil --power 2>/dev/null");
            const result = `Bluetooth: ${bt === "1" ? "ON" : "OFF"}`;
            log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
            return result;
          } catch (err) {
            log.tool.warn("system-controls: bluetooth status failed", err);
            return "Bluetooth status unavailable. Install: brew install blueutil";
          }
        }

        // ── System actions ──
        case "lock_screen": {
          log.tool.debug("system_controls.execute: locking screen via AppleScript keystroke");
          await shell(
            'osascript -e \'tell application "System Events" to keystroke "q" using {control down, command down}\'',
          );
          const result = "Screen locked.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "start_screensaver": {
          log.tool.debug("system_controls.execute: starting screen saver via open");
          await shell("open -a ScreenSaverEngine");
          const result = "Screen saver started.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "sleep": {
          log.tool.debug("system_controls.execute: putting system to sleep via AppleScript");
          await osa('tell application "System Events" to sleep');
          const result = "Putting system to sleep...";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "restart": {
          return "Use 'sudo shutdown -r now' via shell for restart. Not executed automatically for safety.";
        }
        case "shutdown": {
          return "Use 'sudo shutdown -h now' via shell for shutdown. Not executed automatically for safety.";
        }

        // ── Battery ──
        case "get_battery": {
          log.tool.debug("system_controls.execute: querying battery status via pmset");
          const battery = await shell(
            "pmset -g batt | grep -Eo '\\d+%' | head -1 || echo 'N/A'",
          );
          const charging = await shell(
            "pmset -g batt | grep -o 'AC Power' || echo 'Battery'",
          );
          const result = `Battery: ${battery} (${charging})`;
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        // ── Trash ──
        case "empty_trash": {
          log.tool.debug("system_controls.execute: emptying trash via Finder AppleScript");
          await osa('tell application "Finder" to empty trash');
          const result = "Trash emptied.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        // ── Do Not Disturb ──
        case "dnd_on": {
          log.tool.debug("system_controls.execute: enabling Do Not Disturb via shortcuts/defaults");
          await shell(
            "shortcuts run 'Turn On Do Not Disturb' 2>/dev/null || " +
              "defaults -currentHost write com.apple.notificationcenterui doNotDisturb -boolean true && killall NotificationCenter 2>/dev/null",
          );
          const result = "Do Not Disturb enabled.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }
        case "dnd_off": {
          log.tool.debug("system_controls.execute: disabling Do Not Disturb via shortcuts/defaults");
          await shell(
            "shortcuts run 'Turn Off Do Not Disturb' 2>/dev/null || " +
              "defaults -currentHost write com.apple.notificationcenterui doNotDisturb -boolean false && killall NotificationCenter 2>/dev/null",
          );
          const result = "Do Not Disturb disabled.";
          log.tool.debug("system_controls.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        default:
          return (
            `Unknown action: "${action}". Available:\n` +
            `  Audio: get_volume, set_volume, mute, unmute\n` +
            `  Display: get_brightness, set_brightness\n` +
            `  Appearance: dark_mode_on, dark_mode_off, get_dark_mode\n` +
            `  Network: wifi_on, wifi_off, wifi_status, wifi_network, bluetooth_on, bluetooth_off, bluetooth_status\n` +
            `  System: lock_screen, start_screensaver, sleep, get_battery, empty_trash\n` +
            `  Focus: dnd_on, dnd_off`
          );
      }
    } catch (error) {
      log.tool.error("system_controls.execute: failed", error instanceof Error ? error : new Error(String(error)), { action });
      const msg = error instanceof Error ? error.message : String(error);
      return `Error (${action}): ${msg}`;
    }
  },
};
