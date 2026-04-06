/**
 * StackOwl — AirDrop Tool
 *
 * Share files wirelessly to nearby Apple devices via AirDrop.
 *
 * Architecture:
 *   macOS NSSharingService API (via JXA/AppleScript ObjC bridge).
 *   `NSSharingService.performWithItems({fileURL})` triggers the native
 *   AirDrop share sheet — the user picks the recipient device in the UI.
 *
 *   There is no programmatic way to auto-select a recipient (Apple intentionally
 *   requires user confirmation for security). The tool opens the picker,
 *   then the user taps their device name to accept.
 *
 * No special permissions needed beyond normal file access.
 * AirDrop requires Wi-Fi and Bluetooth to be enabled.
 */

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { existsSync, statSync } from "node:fs";
import { resolve, isAbsolute, basename } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";

const execFileAsync = promisify(execFile);

export const AirDropTool: ToolImplementation = {
  definition: {
    name: "airdrop",
    description:
      "Share a file to nearby Apple devices (iPhone, iPad, Mac) via AirDrop. " +
      "Opens the native AirDrop share sheet — the user selects the recipient device. " +
      "Works for any file: images, PDFs, videos, documents, archives. " +
      "Requires Wi-Fi and Bluetooth to be enabled. macOS only.",
    parameters: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description:
            "Absolute or workspace-relative path to the file to share via AirDrop. " +
            "Example: '/Users/me/Desktop/photo.jpg' or 'workspace/downloads/report.pdf'",
        },
      },
      required: ["file_path"],
    },
  },

  category: "system",

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const filePath = args.file_path as string | undefined;
    if (!filePath) return "Error: airdrop requires file_path parameter.";

    const cwd = context.cwd || process.cwd();
    const absPath = isAbsolute(filePath)
      ? filePath
      : resolve(cwd, filePath);

    if (!existsSync(absPath)) {
      return `Error: file not found at "${absPath}".`;
    }

    const stat = statSync(absPath);
    if (!stat.isFile()) {
      return `Error: "${absPath}" is a directory, not a file. AirDrop requires a single file.`;
    }

    const fileName = basename(absPath);
    const sizeKb = Math.round(stat.size / 1024);

    // Use NSSharingService via JXA ObjC bridge to trigger the AirDrop share sheet.
    // This is the only reliable programmatic path — AirDrop has no CLI interface.
    const escapedPath = absPath.replace(/\\/g, "\\\\").replace(/"/g, '\\"');

    const script = `
ObjC.import('AppKit');
ObjC.import('Foundation');

var fileURL = $.NSURL.fileURLWithPath("${escapedPath}");
var items = [fileURL];

var services = $.NSSharingService.sharingServicesForItems(items);
var airdropSvc = null;

for (var i = 0; i < services.count; i++) {
  var svc = services.objectAtIndex(i);
  var title = ObjC.unwrap(svc.title);
  if (title === "AirDrop") {
    airdropSvc = svc;
    break;
  }
}

if (!airdropSvc) {
  throw new Error("AirDrop not available. Check that Wi-Fi and Bluetooth are enabled.");
}

airdropSvc.performWithItems(items);
"ok"
`;

    try {
      const { stdout } = await execFileAsync(
        "osascript",
        ["-l", "JavaScript", "-e", script],
        { timeout: 15_000 },
      );

      if (stdout.trim() === "ok") {
        return (
          `AirDrop share sheet opened for: ${fileName} (${sizeKb}KB)\n` +
          `The AirDrop picker is now visible — select a nearby device to send the file.\n` +
          `The recipient must accept the transfer on their device.`
        );
      }

      return `AirDrop initiated for ${fileName}. Check for the share sheet on screen.`;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);

      if (msg.includes("AirDrop not available")) {
        return (
          "AirDrop is not available.\n" +
          "Ensure:\n" +
          "  • Wi-Fi is enabled (System Settings → Wi-Fi)\n" +
          "  • Bluetooth is enabled (System Settings → Bluetooth)\n" +
          "  • AirDrop is set to 'Everyone' or 'Contacts Only' in Finder → AirDrop"
        );
      }

      return `AirDrop error: ${msg}`;
    }
  },
};
