import { join } from "node:path";
import { SkillInstaller, parseInstallSource } from "../skills/installer.js";
import { ClawHubClient } from "../skills/clawhub.js";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { ToolDefinition } from "../providers/base.js";

export class SkillInstallTool implements ToolImplementation {
  definition: ToolDefinition = {
    name: "install_skill",
    description:
      "Install a skill from ClawHub, GitHub, or a local path, then activate it immediately in this session. " +
      "Sources: bare slug `user/skill-name` or `clawhub:user/skill-name` (ClawHub); " +
      "`github:user/repo/path/to/skill` or `github:user/repo/path@branch` (GitHub); " +
      "`./relative/path` or `/absolute/path` (local). " +
      "After a successful install the skill is ready to use — no restart needed.",
    parameters: {
      type: "object",
      properties: {
        source: {
          type: "string",
          description:
            "Install source. Examples: `ivangdavila/self-improving`, " +
            "`github:some-user/skills-repo/my-skill`, `./workspace/skills/my-skill`",
        },
      },
      required: ["source"],
    },
  };

  category = "filesystem" as const;
  source = "builtin";

  constructor(private readonly workspacePath: string) {}

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const sourceArg = (args.source as string | undefined)?.trim();
    if (!sourceArg) return "Error: `source` is required.";

    const parsed = parseInstallSource(sourceArg);
    const skillsDir = join(this.workspacePath, "skills");

    try {
      if (parsed.type === "github") {
        const installer = new SkillInstaller(this.workspacePath);
        await installer.fromGitHub(parsed.rawUrl, parsed.skillName);
      } else if (parsed.type === "local") {
        const installer = new SkillInstaller(this.workspacePath);
        await installer.fromLocal(parsed.localPath);
      } else {
        const client = new ClawHubClient();
        await client.install(parsed.slug, skillsDir);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return `Failed to install "${sourceArg}": ${msg}`;
    }

    const registry = context.engineContext?.skillsRegistry;
    if (registry) {
      await registry.loadFromDirectory(skillsDir);
      return `✓ Installed "${parsed.skillName}" and loaded it into this session. The skill is now active.`;
    }

    return `✓ Installed "${parsed.skillName}". Restart the assistant to activate it.`;
  }
}
