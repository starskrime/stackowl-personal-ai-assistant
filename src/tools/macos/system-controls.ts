import type { ToolImplementation, ToolContext } from "../registry.js";

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
          const vol = await osa("output volume of (get volume settings)");
          const muted = await osa("output muted of (get volume settings)");
          return `Volume: ${vol}%${muted === "true" ? " (muted)" : ""}`;
        }
        case "set_volume": {
          if (value === undefined || value < 0 || value > 100)
            return "Error: set_volume requires value 0-100.";
          await osa(`set volume output volume ${value}`);
          return `Volume set to ${value}%`;
        }
        case "mute": {
          await osa("set volume output muted true");
          return "Audio muted.";
        }
        case "unmute": {
          await osa("set volume output muted false");
          return "Audio unmuted.";
        }

        // ── Brightness ──
        case "get_brightness": {
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
            } catch {
              return "Brightness: Could not read. Install: brew install brightness";
            }
          }
          return `Brightness: ${Math.round(parseFloat(br) * 100)}%`;
        }
        case "set_brightness": {
          if (value === undefined || value < 0 || value > 100)
            return "Error: set_brightness requires value 0-100.";
          const brVal = (value / 100).toFixed(2);
          try {
            await shell(`brightness ${brVal}`);
            return `Brightness set to ${value}%`;
          } catch {
            return `Error: Could not set brightness. Install: brew install brightness`;
          }
        }

        // ── Dark Mode ──
        case "dark_mode_on": {
          await osa(
            'tell application "System Events" to tell appearance preferences to set dark mode to true',
          );
          return "Dark mode enabled.";
        }
        case "dark_mode_off": {
          await osa(
            'tell application "System Events" to tell appearance preferences to set dark mode to false',
          );
          return "Dark mode disabled.";
        }
        case "get_dark_mode": {
          const dm = await osa(
            'tell application "System Events" to tell appearance preferences to get dark mode',
          );
          return `Dark mode: ${dm === "true" ? "ON" : "OFF"}`;
        }

        // ── Wi-Fi ──
        case "wifi_on": {
          await shell("networksetup -setairportpower en0 on");
          return "Wi-Fi turned on.";
        }
        case "wifi_off": {
          await shell("networksetup -setairportpower en0 off");
          return "Wi-Fi turned off.";
        }
        case "wifi_status": {
          const status = await shell("networksetup -getairportpower en0");
          return status;
        }
        case "wifi_network": {
          try {
            const network = await shell(
              "networksetup -getairportnetwork en0 2>/dev/null || " +
              "ipconfig getifaddr en0 2>/dev/null || echo 'Not connected'",
            );
            return `Wi-Fi: ${network}`;
          } catch {
            return "Wi-Fi: Not connected or could not detect.";
          }
        }

        // ── Bluetooth ──
        case "bluetooth_on": {
          await shell("blueutil --power 1 2>/dev/null || echo 'Install: brew install blueutil'");
          return "Bluetooth turned on.";
        }
        case "bluetooth_off": {
          await shell("blueutil --power 0 2>/dev/null || echo 'Install: brew install blueutil'");
          return "Bluetooth turned off.";
        }
        case "bluetooth_status": {
          try {
            const bt = await shell("blueutil --power 2>/dev/null");
            return `Bluetooth: ${bt === "1" ? "ON" : "OFF"}`;
          } catch {
            return "Bluetooth status unavailable. Install: brew install blueutil";
          }
        }

        // ── System actions ──
        case "lock_screen": {
          await shell(
            'osascript -e \'tell application "System Events" to keystroke "q" using {control down, command down}\'',
          );
          return "Screen locked.";
        }
        case "start_screensaver": {
          await shell("open -a ScreenSaverEngine");
          return "Screen saver started.";
        }
        case "sleep": {
          await osa('tell application "System Events" to sleep');
          return "Putting system to sleep...";
        }
        case "restart": {
          return "Use 'sudo shutdown -r now' via shell for restart. Not executed automatically for safety.";
        }
        case "shutdown": {
          return "Use 'sudo shutdown -h now' via shell for shutdown. Not executed automatically for safety.";
        }

        // ── Battery ──
        case "get_battery": {
          const battery = await shell(
            "pmset -g batt | grep -Eo '\\d+%' | head -1 || echo 'N/A'",
          );
          const charging = await shell(
            "pmset -g batt | grep -o 'AC Power' || echo 'Battery'",
          );
          return `Battery: ${battery} (${charging})`;
        }

        // ── Trash ──
        case "empty_trash": {
          await osa(
            'tell application "Finder" to empty trash',
          );
          return "Trash emptied.";
        }

        // ── Do Not Disturb ──
        case "dnd_on": {
          await shell(
            "shortcuts run 'Turn On Do Not Disturb' 2>/dev/null || " +
            "defaults -currentHost write com.apple.notificationcenterui doNotDisturb -boolean true && killall NotificationCenter 2>/dev/null",
          );
          return "Do Not Disturb enabled.";
        }
        case "dnd_off": {
          await shell(
            "shortcuts run 'Turn Off Do Not Disturb' 2>/dev/null || " +
            "defaults -currentHost write com.apple.notificationcenterui doNotDisturb -boolean false && killall NotificationCenter 2>/dev/null",
          );
          return "Do Not Disturb disabled.";
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
      const msg = error instanceof Error ? error.message : String(error);
      return `Error (${action}): ${msg}`;
    }
  },
};
