import { useState } from "react";
import { execSync } from "child_process";
import { log } from "../../../logger.js";

export function useGitBranch(): string | null {
  const [branch] = useState<string | null>(() => {
    try {
      return execSync("git rev-parse --abbrev-ref HEAD", {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
        timeout: 2000,
      }).trim();
    } catch (err) {
      log.cli.error(
        "useGitBranch: git branch detection failed",
        err instanceof Error ? err : new Error(String(err))
      );
      return null;
    }
  });
  // branch is read once synchronously in the useState initializer — no useEffect needed
  return branch;
}
