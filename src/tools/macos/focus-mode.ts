import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

export const FocusModeTool: ToolImplementation = {
  definition: {
    name: "focus_mode",
    description: "Check or toggle macOS Focus/Do Not Disturb mode.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["status", "on", "off"],
          description:
            "Action to perform: check current status, turn on, or turn off Focus/DND.",
        },
      },
      required: ["action"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const action = args.action as string;

    try {
      switch (action) {
        case "status": {
          try {
            const { stdout } = await execAsync(
              `defaults read com.apple.controlcenter "NSStatusItem Visible FocusModes" 2>/dev/null || echo "unknown"`,
              { timeout: 15000 },
            );
            const trimmed = stdout.trim();

            // Also check via plutil for DND assertion
            try {
              const { stdout: dndOut } = await execAsync(
                `plutil -extract dnd_prefs xml1 -o - ~/Library/Preferences/com.apple.ncprefs.plist 2>/dev/null | grep -c "true" || echo "0"`,
                { timeout: 15000 },
              );
              const dndActive = parseInt(dndOut.trim(), 10) > 0;
              return `Focus/DND status: ${dndActive ? "ON" : "OFF"} (control center visibility: ${trimmed})`;
            } catch {
              return `Focus mode control center visibility: ${trimmed}`;
            }
          } catch {
            return "Unable to determine Focus/DND status. macOS privacy settings may be restricting access.";
          }
        }

        case "on": {
          try {
            await execAsync(
              `shortcuts run "Focus" 2>/dev/null || osascript -e 'do shell script "defaults -currentHost write com.apple.notificationcenterui dndStart -float 0; defaults -currentHost write com.apple.notificationcenterui dndEnd -float 1440; defaults -currentHost write com.apple.notificationcenterui doNotDisturb -boolean true; killall NotificationCenter 2>/dev/null || true"'`,
              { timeout: 15000 },
            );
            return "Focus/Do Not Disturb mode turned ON.";
          } catch {
            return "Unable to enable Focus mode. You may need to set up a 'Focus' shortcut in the Shortcuts app, or enable it manually.";
          }
        }

        case "off": {
          try {
            await execAsync(
              `osascript -e 'do shell script "defaults -currentHost write com.apple.notificationcenterui doNotDisturb -boolean false; killall NotificationCenter 2>/dev/null || true"'`,
              { timeout: 15000 },
            );
            return "Focus/Do Not Disturb mode turned OFF.";
          } catch {
            return "Unable to disable Focus mode. You may need to disable it manually in System Settings.";
          }
        }

        default:
          return `Error: Unknown action "${action}". Use "status", "on", or "off".`;
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error with Focus mode: ${msg}`;
    }
  },
};
