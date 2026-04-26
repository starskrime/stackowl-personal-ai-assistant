import { dirname } from "node:path";
import { ClawHubClient, type ClawHubSkill } from "./clawhub.js";
import { SkillInstaller } from "./installer.js";
import type { SkillsRegistry } from "./registry.js";

export interface WizardResponse {
  text: string;
  done: boolean;
  inlineKeyboard?: { text: string; data: string }[][];
}

type WizardStep =
  | "choose_source"
  | "search_clawhub"
  | "pick_clawhub"
  | "enter_github"
  | "enter_local";

interface WizardState {
  step: WizardStep;
  searchResults?: ClawHubSkill[];
}

const SOURCE_MENU_TEXT =
  "Choose a source to install from:\n\n" +
  "1. ClawHub — search the skill marketplace\n" +
  "2. GitHub — install from a GitHub repo path\n" +
  "3. Local — install from a local folder path\n\n" +
  "Type a number or /cancel to exit.";

const SOURCE_KEYBOARD: WizardResponse["inlineKeyboard"] = [
  [
    { text: "ClawHub", data: "wiz:clawhub" },
    { text: "GitHub", data: "wiz:github" },
    { text: "Local", data: "wiz:local" },
  ],
];

export class SkillInstallWizard {
  private state: WizardState = { step: "choose_source" };

  constructor(
    private readonly skillsDir: string,
    private readonly clawHubClient: ClawHubClient,
    private readonly registry?: SkillsRegistry,
  ) {}

  start(): WizardResponse {
    return { text: SOURCE_MENU_TEXT, done: false, inlineKeyboard: SOURCE_KEYBOARD };
  }

  async step(input: string): Promise<WizardResponse> {
    if (input.trim().toLowerCase() === "/cancel") {
      return { text: "Cancelled.", done: true };
    }
    switch (this.state.step) {
      case "choose_source":  return this.handleChooseSource(input.trim());
      case "search_clawhub": return this.handleSearchClawHub(input.trim());
      case "pick_clawhub":   return this.handlePickClawHub(input.trim());
      case "enter_github":   return this.handleEnterGitHub(input.trim());
      case "enter_local":    return this.handleEnterLocal(input.trim());
    }
  }

  private handleChooseSource(input: string): WizardResponse {
    const lower = input.toLowerCase();
    if (lower === "1" || lower === "clawhub" || lower === "wiz:clawhub") {
      this.state.step = "search_clawhub";
      return { text: "Search ClawHub — enter a keyword (e.g. git, docker, pdf):", done: false };
    }
    if (lower === "2" || lower === "github" || lower === "wiz:github") {
      this.state.step = "enter_github";
      return {
        text: "Enter the GitHub path:\nFormat: `github:user/repo/path/to/skill` or `github:user/repo/path@branch`\n\nOr /cancel to exit.",
        done: false,
      };
    }
    if (lower === "3" || lower === "local" || lower === "wiz:local") {
      this.state.step = "enter_local";
      return {
        text: "Enter the local path:\nFormat: `./relative/path` or `/absolute/path` (must contain a SKILL.md file)\n\nOr /cancel to exit.",
        done: false,
      };
    }
    return {
      text: "Please enter 1, 2, or 3.\n\n" + SOURCE_MENU_TEXT,
      done: false,
      inlineKeyboard: SOURCE_KEYBOARD,
    };
  }

  private async handleSearchClawHub(query: string): Promise<WizardResponse> {
    // Slug pattern (user/skill-name) — install directly, skip broken search API
    if (/^[\w.-]+(\/[\w.-]+)+$/.test(query)) {
      return this.doInstallClawHub(query);
    }

    try {
      const result = await this.clawHubClient.search(query, 5);
      if (result.skills.length === 0) {
        return { text: `No skills found for "${query}". Try another keyword:`, done: false };
      }
      this.state.searchResults = result.skills;
      this.state.step = "pick_clawhub";
      const listText = result.skills
        .map((s, i) => `${i + 1}. **${s.name}** — ${s.description}`)
        .join("\n");
      const keyboard: WizardResponse["inlineKeyboard"] = result.skills.map((s) => [
        { text: s.name, data: `wiz:pick:${s.slug}` },
      ]);
      return {
        text: `Found ${result.skills.length} skill${result.skills.length > 1 ? "s" : ""}:\n\n${listText}\n\nType a number to install, or /cancel to exit.`,
        done: false,
        inlineKeyboard: keyboard,
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        text: `Search failed: ${msg}\n\nTry a different keyword, enter a slug (user/skill-name) directly, or /cancel:`,
        done: false,
      };
    }
  }

  private async handlePickClawHub(input: string): Promise<WizardResponse> {
    const lower = input.toLowerCase();
    let slug: string | undefined;

    if (lower.startsWith("wiz:pick:")) {
      slug = input.slice("wiz:pick:".length);
    } else {
      const num = parseInt(input, 10);
      if (!isNaN(num) && this.state.searchResults) {
        slug = this.state.searchResults[num - 1]?.slug;
      } else {
        slug = input;
      }
    }

    if (!slug) {
      const keyboard: WizardResponse["inlineKeyboard"] = (this.state.searchResults ?? []).map(
        (s) => [{ text: s.name, data: `wiz:pick:${s.slug}` }],
      );
      return {
        text: "Please type a number or tap a skill to install, or /cancel to exit.",
        done: false,
        inlineKeyboard: keyboard,
      };
    }

    return this.doInstallClawHub(slug);
  }

  private async doInstallClawHub(slug: string): Promise<WizardResponse> {
    try {
      await this.clawHubClient.install(slug, this.skillsDir);
      await this.registry?.loadFromDirectory(this.skillsDir);
      return { text: `✓ Installed "${slug}" — ready to use.`, done: true };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { text: `Failed to install "${slug}": ${msg}`, done: true };
    }
  }

  private async handleEnterGitHub(input: string): Promise<WizardResponse> {
    const normalized = input.startsWith("github:") ? input : `github:${input}`;
    const rest = normalized.slice("github:".length);
    const atIdx = rest.indexOf("@");
    const pathPart = atIdx === -1 ? rest : rest.slice(0, atIdx);
    const branch = atIdx === -1 ? "main" : rest.slice(atIdx + 1);
    const parts = pathPart.split("/");
    if (parts.length < 3) {
      return {
        text: "Invalid GitHub path. Expected: `github:user/repo/path/to/skill`\n\nTry again or /cancel:",
        done: false,
      };
    }
    const [user, repo, ...skillParts] = parts;
    const skillPath = skillParts.join("/");
    const skillName = skillParts[skillParts.length - 1]!;
    const rawUrl = `https://raw.githubusercontent.com/${user}/${repo}/${branch}/${skillPath}/SKILL.md`;
    try {
      const installer = new SkillInstaller(dirname(this.skillsDir));
      await installer.fromGitHub(rawUrl, skillName);
      await this.registry?.loadFromDirectory(this.skillsDir);
      return { text: `✓ Installed "${skillName}" from GitHub — ready to use.`, done: true };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        text: `Could not fetch skill from GitHub: ${msg}\n\nTry again or /cancel:`,
        done: false,
      };
    }
  }

  private async handleEnterLocal(input: string): Promise<WizardResponse> {
    try {
      const installer = new SkillInstaller(dirname(this.skillsDir));
      await installer.fromLocal(input);
      await this.registry?.loadFromDirectory(this.skillsDir);
      const { basename } = await import("node:path");
      const skillName = basename(input);
      return { text: `✓ Installed "${skillName}" from local path — ready to use.`, done: true };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        text: `Could not install from local path: ${msg}\n\nTry again or /cancel:`,
        done: false,
      };
    }
  }
}
