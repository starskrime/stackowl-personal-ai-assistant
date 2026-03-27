import type { ToolImplementation, ToolContext } from "../registry.js";

export const ArchiveTool: ToolImplementation = {
  definition: {
    name: "archive",
    description:
      "Create and extract archives: zip, tar, tar.gz, tar.bz2. " +
      "Compress files/folders or extract existing archives.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description: "Action: compress or extract",
        },
        paths: {
          type: "string",
          description:
            "For compress: comma-separated file/folder paths to include. " +
            "For extract: path to the archive file.",
        },
        output: {
          type: "string",
          description:
            "For compress: output archive path (e.g., 'backup.zip', 'files.tar.gz'). " +
            "For extract: output directory (default: current directory).",
        },
        format: {
          type: "string",
          description:
            "Archive format: zip (default), tar, tar.gz, tar.bz2. Auto-detected from output extension.",
        },
        password: {
          type: "string",
          description: "Password for zip encryption (compress only)",
        },
      },
      required: ["action", "paths"],
    },
  },

  category: "filesystem",

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const action = String(args.action);
    const paths = String(args.paths);
    const output = args.output as string | undefined;
    const password = args.password as string | undefined;
    const cwd = context.cwd || process.cwd();

    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const { resolve } = await import("node:path");
    const { statSync } = await import("node:fs");
    const exec = promisify(execFile);

    const shell = async (cmd: string): Promise<string> => {
      const { stdout } = await exec("bash", ["-c", cmd], {
        timeout: 120000,
        cwd,
      });
      return stdout.trim();
    };

    try {
      if (action === "compress") {
        const inputPaths = paths.split(",").map((p) => p.trim());
        const outPath = output || `archive_${Date.now()}.zip`;
        const resolvedOut = resolve(cwd, outPath);

        // Detect format from extension
        let format = (args.format as string) || "";
        if (!format) {
          if (outPath.endsWith(".tar.gz") || outPath.endsWith(".tgz"))
            format = "tar.gz";
          else if (outPath.endsWith(".tar.bz2") || outPath.endsWith(".tbz2"))
            format = "tar.bz2";
          else if (outPath.endsWith(".tar")) format = "tar";
          else format = "zip";
        }

        const quotedPaths = inputPaths
          .map((p) => `"${resolve(cwd, p)}"`)
          .join(" ");

        switch (format) {
          case "zip": {
            const pwFlag = password ? `-P "${password}"` : "";
            await shell(`zip -r ${pwFlag} "${resolvedOut}" ${quotedPaths}`);
            break;
          }
          case "tar":
            await shell(`tar -cf "${resolvedOut}" ${quotedPaths}`);
            break;
          case "tar.gz":
            await shell(`tar -czf "${resolvedOut}" ${quotedPaths}`);
            break;
          case "tar.bz2":
            await shell(`tar -cjf "${resolvedOut}" ${quotedPaths}`);
            break;
          default:
            return `Error: Unsupported format "${format}". Use: zip, tar, tar.gz, tar.bz2`;
        }

        const stats = statSync(resolvedOut);
        const sizeMB = (stats.size / 1024 / 1024).toFixed(2);
        return (
          `📦 Archive created: ${resolvedOut}\n` +
          `Format: ${format}\n` +
          `Size: ${sizeMB} MB\n` +
          `Files: ${inputPaths.join(", ")}` +
          (password ? "\n🔒 Password protected" : "")
        );
      }

      if (action === "extract") {
        const archivePath = resolve(cwd, paths);
        const outDir = output ? resolve(cwd, output) : cwd;

        // Auto-detect format
        if (archivePath.endsWith(".zip")) {
          await shell(`unzip -o "${archivePath}" -d "${outDir}"`);
        } else if (
          archivePath.endsWith(".tar.gz") ||
          archivePath.endsWith(".tgz")
        ) {
          await shell(`tar -xzf "${archivePath}" -C "${outDir}"`);
        } else if (
          archivePath.endsWith(".tar.bz2") ||
          archivePath.endsWith(".tbz2")
        ) {
          await shell(`tar -xjf "${archivePath}" -C "${outDir}"`);
        } else if (archivePath.endsWith(".tar")) {
          await shell(`tar -xf "${archivePath}" -C "${outDir}"`);
        } else if (archivePath.endsWith(".7z")) {
          await shell(
            `7z x "${archivePath}" -o"${outDir}" -y 2>/dev/null || echo "Install: brew install p7zip"`,
          );
        } else if (archivePath.endsWith(".rar")) {
          await shell(
            `unrar x "${archivePath}" "${outDir}" 2>/dev/null || echo "Install: brew install unrar"`,
          );
        } else {
          return `Error: Cannot detect archive format from extension. Supported: .zip, .tar, .tar.gz, .tgz, .tar.bz2, .7z, .rar`;
        }

        return `📂 Extracted: ${archivePath}\nTo: ${outDir}`;
      }

      return `Error: Unknown action "${action}". Use 'compress' or 'extract'.`;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error (${action}): ${msg}`;
    }
  },
};
