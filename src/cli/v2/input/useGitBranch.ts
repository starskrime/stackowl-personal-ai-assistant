import { useState } from "react";
import { execSync } from "child_process";

export function useGitBranch(): string | null {
  const [branch] = useState<string | null>(() => {
    try {
      return execSync("git rev-parse --abbrev-ref HEAD", {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      }).trim();
    } catch {
      process.stderr.write("[useGitBranch] failed to read git branch\n");
      return null;
    }
  });
  // branch is read once synchronously in the useState initializer — no useEffect needed
  return branch;
}
