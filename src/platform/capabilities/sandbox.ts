import { existsSync, realpathSync } from "node:fs";
import { resolve, sep, extname } from "node:path";
import { log } from "../../logger.js";
import type { Paths, Sandbox, SandboxPolicy, SandboxResult } from "../types.js";

let dockerBypassLogged = false;

export class SandboxImpl implements Sandbox {
  constructor(private readonly paths: Paths) {}

  check(rawPath: string, policy: SandboxPolicy): SandboxResult {
    const resolveSymlinks = policy.resolveSymlinks ?? true;
    const absolute = resolve(rawPath);

    let resolvedPath = absolute;
    if (resolveSymlinks) {
      try {
        resolvedPath = realpathSync(absolute);
      } catch {
        log.tool.debug("sandbox.check: realpath failed, using lexical path", { absolute });
        resolvedPath = absolute;
      }
    }

    const inDocker = process.env.IN_DOCKER === "true" || existsSync("/.dockerenv");
    if (inDocker) {
      if (!dockerBypassLogged) {
        log.tool.info("sandbox.check: Docker bypass active — full filesystem access permitted", {
          reason: "container environment",
        });
        dockerBypassLogged = true;
      }
      return { ok: true, resolvedPath };
    }

    const roots = policy.workspaceRoots.map((r) => {
      try {
        return realpathSync(resolve(r));
      } catch {
        return resolve(r);
      }
    });
    if (policy.allowTempdir) {
      roots.push(this.paths.tempdir());
    }

    const insideRoot = roots.some(
      (root) => resolvedPath === root || resolvedPath.startsWith(root + sep),
    );
    if (!insideRoot) {
      return {
        ok: false,
        resolvedPath,
        reason: "E_OUTSIDE_SANDBOX",
        message: `Access denied: "${resolvedPath}" is outside allowed roots: ${roots.join(", ")}`,
      };
    }

    if (policy.allowExtensions && policy.allowExtensions.length > 0) {
      const ext = extname(resolvedPath).toLowerCase();
      const allowed = policy.allowExtensions.map((e) => e.toLowerCase());
      if (!allowed.includes(ext)) {
        return {
          ok: false,
          resolvedPath,
          reason: "E_EXTENSION_BLOCKED",
          message: `Extension "${ext}" not in allowed list: ${allowed.join(", ")}`,
        };
      }
    }

    return { ok: true, resolvedPath };
  }
}
