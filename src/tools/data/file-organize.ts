/**
 * StackOwl — File Organize Tool
 *
 * Organizes files in a directory by type, date, or size.
 * Preview mode shows the plan before executing.
 */

import { readdir, stat, mkdir, rename } from "node:fs/promises";
import { resolve, extname, join } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";

const TYPE_CATEGORIES: Record<string, string[]> = {
  Documents: [
    ".pdf",
    ".doc",
    ".docx",
    ".txt",
    ".rtf",
    ".odt",
    ".xls",
    ".xlsx",
    ".csv",
    ".ppt",
    ".pptx",
    ".pages",
    ".numbers",
    ".key",
  ],
  Images: [
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".svg",
    ".webp",
    ".ico",
    ".tiff",
    ".heic",
    ".heif",
  ],
  Videos: [".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v"],
  Audio: [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus"],
  Archives: [".zip", ".tar", ".gz", ".rar", ".7z", ".bz2", ".xz", ".tgz"],
  Code: [
    ".js",
    ".ts",
    ".py",
    ".rb",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".go",
    ".rs",
    ".swift",
    ".kt",
    ".cs",
    ".php",
    ".html",
    ".css",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".md",
  ],
};

function getCategoryForExt(ext: string): string {
  const lower = ext.toLowerCase();
  for (const [category, extensions] of Object.entries(TYPE_CATEGORIES)) {
    if (extensions.includes(lower)) return category;
  }
  return "Other";
}

function getSizeCategory(bytes: number): string {
  if (bytes < 100 * 1024) return "Small"; // < 100KB
  if (bytes < 10 * 1024 * 1024) return "Medium"; // < 10MB
  return "Large";
}

interface MoveAction {
  file: string;
  from: string;
  to: string;
}

export const FileOrganizeTool: ToolImplementation = {
  definition: {
    name: "file_organize",
    description:
      "Organize files in a directory by type, date, or size. Preview mode shows the plan before executing.",
    parameters: {
      type: "object",
      properties: {
        directory: {
          type: "string",
          description: "Path to the directory to organize.",
        },
        strategy: {
          type: "string",
          description:
            'Organization strategy: "by_type" (default), "by_date", or "by_size".',
        },
        action: {
          type: "string",
          description:
            '"preview" (default, shows plan) or "execute" (performs the moves).',
        },
      },
      required: ["directory"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const directory = args["directory"] as string;
      if (!directory) return "Error: 'directory' parameter is required.";

      const strategy = (args["strategy"] as string) || "by_type";
      const action = (args["action"] as string) || "preview";

      if (!["by_type", "by_date", "by_size"].includes(strategy)) {
        return `Error: Invalid strategy '${strategy}'. Must be one of: by_type, by_date, by_size`;
      }
      if (!["preview", "execute"].includes(action)) {
        return `Error: Invalid action '${action}'. Must be 'preview' or 'execute'.`;
      }

      const resolvedDir = resolve(_context.cwd, directory);

      let entries: string[];
      try {
        entries = await readdir(resolvedDir);
      } catch {
        return `Error: Cannot read directory: ${resolvedDir}`;
      }

      const moves: MoveAction[] = [];

      for (const entry of entries) {
        const fullPath = join(resolvedDir, entry);
        let fileStat;
        try {
          fileStat = await stat(fullPath);
        } catch {
          continue;
        }

        if (!fileStat.isFile()) continue;

        let targetFolder: string;

        switch (strategy) {
          case "by_type": {
            const ext = extname(entry);
            targetFolder = ext ? getCategoryForExt(ext) : "Other";
            break;
          }
          case "by_date": {
            const mtime = fileStat.mtime;
            const year = mtime.getFullYear();
            const month = String(mtime.getMonth() + 1).padStart(2, "0");
            targetFolder = `${year}-${month}`;
            break;
          }
          case "by_size": {
            targetFolder = getSizeCategory(fileStat.size);
            break;
          }
          default:
            targetFolder = "Other";
        }

        const targetDir = join(resolvedDir, targetFolder);
        const targetPath = join(targetDir, entry);

        if (fullPath !== targetPath) {
          moves.push({
            file: entry,
            from: fullPath,
            to: targetPath,
          });
        }
      }

      if (moves.length === 0) {
        return `No files to organize in ${resolvedDir}.`;
      }

      if (action === "preview") {
        // Group by target folder for clear display
        const grouped: Record<string, string[]> = {};
        for (const move of moves) {
          const folder = move.to.replace(resolvedDir + "/", "").split("/")[0];
          if (!grouped[folder]) grouped[folder] = [];
          grouped[folder].push(move.file);
        }

        let preview = `Organization plan for ${resolvedDir} (strategy: ${strategy}):\n\n`;
        for (const [folder, files] of Object.entries(grouped).sort()) {
          preview += `${folder}/\n`;
          for (const file of files) {
            preview += `  - ${file}\n`;
          }
        }
        preview += `\nTotal: ${moves.length} files to move.\nRun with action: "execute" to apply.`;
        return preview;
      }

      // Execute the moves
      let movedCount = 0;
      const errors: string[] = [];

      for (const move of moves) {
        try {
          const targetDir = resolve(move.to, "..");
          await mkdir(targetDir, { recursive: true });
          await rename(move.from, move.to);
          movedCount++;
        } catch (err: any) {
          errors.push(`Failed to move ${move.file}: ${err.message}`);
        }
      }

      let result = `Organized ${movedCount} of ${moves.length} files in ${resolvedDir} (strategy: ${strategy}).`;
      if (errors.length > 0) {
        result += `\n\nErrors:\n${errors.join("\n")}`;
      }
      return result;
    } catch (error: any) {
      return `Error organizing files: ${error.message ?? String(error)}`;
    }
  },
};
