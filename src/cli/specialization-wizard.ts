import chalk from "chalk";
import { mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { TerminalRenderer } from "./renderer.js";
import { existsSync } from "node:fs";

const YB = chalk.yellow.bold;
const D = chalk.dim;
const W = chalk.white;
const C = chalk.cyan;
const G = chalk.green;
const R = chalk.red;

function sep() { return D("─".repeat(40)); }

interface WizardData {
  name?: string;
  role?: string;
  emoji?: string;
  challengeLevel?: string;
  verbosity?: string;
  tone?: string;
  expertise?: string[];
  allowedTools?: string[];
  deniedTools?: string[];
  capabilityConstraints?: string[];
  provider?: string;
  model?: string;
  maxTokens?: number;
  skills?: string[];
}

type StepId =
  | "welcome"
  | "name"
  | "role"
  | "emoji"
  | "challenge_level"
  | "verbosity"
  | "tone"
  | "expertise"
  | "allowed_tools"
  | "denied_tools"
  | "capability_constraints"
  | "model_provider"
  | "model_name"
  | "model_tokens"
  | "skills"
  | "review"
  | "done";

const CHALLENGE_OPTIONS = [
  "low — gentle guidance, never pushes back",
  "medium — balanced,偶尔 challenges your assumptions",
  "high — actively debates, demands evidence",
  "relentless — won't let you get away with vague reasoning",
];

const VERBOSITY_OPTIONS = [
  "concise — short answers, gets to the point",
  "balanced — thorough when needed, brief when not",
  "verbose — detailed explanations, examples, context",
];

export class SpecializationCreateWizard {
  private _step: StepId = "welcome";
  private _data: WizardData = {};

  getCurrentStep(): StepId {
    return this._step;
  }

  getSpec(): WizardData {
    return { ...this._data };
  }

  start(ui: TerminalRenderer): void {
    this._step = "welcome";
    this._showStep(ui);
  }

  async step(input: string, ui: TerminalRenderer): Promise<boolean> {
    if (input.toLowerCase() === "cancel" || input.toLowerCase() === "abort") {
      ui.printLines(["", R("✕ Wizard cancelled."), ""]);
      return true;
    }

    const done = await this._handle(input, ui);
    return done;
  }

  private _showStep(ui: TerminalRenderer): void {
    switch (this._step) {
      case "welcome":
        ui.printLines([
          "",
          YB("Create Specialized Owl"),
          sep(),
          D("This wizard will help you create a new specialized owl."),
          D("Each owl has its own folder with configuration, credentials,"),
          D("permissions, and skill whitelists."),
          "",
          C("  Press Enter to begin, or 'cancel' to exit"),
          "",
        ]);
        return;

      case "name":
        ui.printLines([
          "",
          YB("Owl Identity"),
          sep(),
          "🦉  What should this owl be called?",
          D("  (e.g. TradingBot, CodeReviewer, ResearchAssistant)"),
          "",
          C("  Type the name and press Enter:"),
          "",
        ]);
        break;

      case "role":
        ui.printLines([
          "",
          YB("Owl Role"),
          sep(),
          "🦉  What is this owl's role?",
          D("  Describe what this owl helps with."),
          D("  (e.g. 'Stock trading assistant', 'Code review specialist')"),
          "",
          C("  Type the role description and press Enter:"),
          "",
        ]);
        break;

      case "emoji":
        ui.printLines([
          "",
          YB("Owl Emoji"),
          sep(),
          "🦉  Pick an emoji for this owl:",
          "",
          C("  📈") + "  stocks/trading    " + C("🔧") + "  engineering    " + C("📊") + "  data/analytics",
          C("  🔬") + "  research         " + C("✍️") + "  writing        " + C("💡") + "  ideas",
          C("  🛡️") + "  security         " + C("🎨") + "  design         " + C("🤖") + "  automation",
          "",
          C("  Type an emoji and press Enter:"),
          "",
        ]);
        break;

      case "challenge_level":
        ui.printLines([
          "",
          YB("Personality — Challenge Level"),
          sep(),
          "🦉  How confrontational should this owl be?",
          "",
          C("  1") + D("  ") + CHALLENGE_OPTIONS[0],
          C("  2") + D("  ") + CHALLENGE_OPTIONS[1],
          C("  3") + D("  ") + CHALLENGE_OPTIONS[2],
          C("  4") + D("  ") + CHALLENGE_OPTIONS[3],
          "",
          C("  Type 1-4 and press Enter:"),
          "",
        ]);
        break;

      case "verbosity":
        ui.printLines([
          "",
          YB("Personality — Verbosity"),
          sep(),
          "🦉  How verbose should this owl be?",
          "",
          C("  1") + D("  ") + VERBOSITY_OPTIONS[0],
          C("  2") + D("  ") + VERBOSITY_OPTIONS[1],
          C("  3") + D("  ") + VERBOSITY_OPTIONS[2],
          "",
          C("  Type 1-3 and press Enter:"),
          "",
        ]);
        break;

      case "tone":
        ui.printLines([
          "",
          YB("Personality — Tone"),
          sep(),
          "🦉  What tone should this owl use?",
          D("  (e.g. 'casual and friendly', 'formal and precise', 'encouraging')"),
          "",
          C("  Type the tone and press Enter:"),
          "",
        ]);
        break;

      case "expertise":
        ui.printLines([
          "",
          YB("Expertise Domains"),
          sep(),
          "🦉  What topics is this owl an expert in?",
          D("  Enter topics separated by commas."),
          D("  (e.g. 'stock market, portfolio management, technical analysis')"),
          "",
          C("  Type expertise areas and press Enter:"),
          "",
        ]);
        break;

      case "allowed_tools":
        ui.printLines([
          "",
          YB("Permissions — Allowed Tools"),
          sep(),
          "🦉  Which tools can this owl use?",
          D("  Leave blank for all tools, or enter tool names separated by commas."),
          D("  Common tools: shell, calculator, web_search, read_file, write_file"),
          "",
          C("  Type allowed tools (or Enter for all):"),
          "",
        ]);
        break;

      case "denied_tools":
        ui.printLines([
          "",
          YB("Permissions — Denied Tools"),
          sep(),
          "🦉  Which tools should this owl NEVER use?",
          D("  Enter tool names separated by commas, or leave blank for none."),
          D("  (e.g. 'shell, delete_file' to prevent destructive operations)"),
          "",
          C("  Type denied tools (or Enter for none):"),
          "",
        ]);
        break;

      case "capability_constraints":
        ui.printLines([
          "",
          YB("Permissions — Capability Constraints"),
          sep(),
          "🦉  What should this owl be explicitly prevented from doing?",
          D("  These constraints will be injected into the system prompt."),
          D("  (e.g. 'Cannot execute trades', 'Cannot access personal finances')"),
          "",
          C("  Type constraints (or Enter for none):"),
          "",
        ]);
        break;

      case "model_provider":
        ui.printLines([
          "",
          YB("Model Configuration"),
          sep(),
          "🦉  Which AI provider should this owl use?",
          "",
          C("  1") + D("  Anthropic (Claude)"),
          C("  2") + D("  OpenAI (GPT-4)"),
          C("  3") + D("  Ollama (local)"),
          C("  4") + D("  Same as default (use global setting)"),
          "",
          C("  Type 1-4 and press Enter:"),
          "",
        ]);
        break;

      case "model_name":
        ui.printLines([
          "",
          YB("Model Configuration"),
          sep(),
          "🦉  Which model should this owl use?",
          D("  Leave blank for default model, or specify a model name."),
          D("  (e.g. 'claude-sonnet-4-6', 'gpt-4o', 'llama3.2')"),
          "",
          C("  Type model name (or Enter for default):"),
          "",
        ]);
        break;

      case "model_tokens":
        ui.printLines([
          "",
          YB("Model Configuration"),
          sep(),
          "🦉  Maximum tokens for responses?",
          D("  Leave blank for default (4096), or specify a number."),
          D("  Lower values = shorter responses, higher = longer responses."),
          "",
          C("  Type max tokens (or Enter for default):"),
          "",
        ]);
        break;

      case "skills":
        ui.printLines([
          "",
          YB("Skills"),
          sep(),
          "🦉  Which skills should this owl have access to?",
          D("  Leave blank for all skills, or enter skill names separated by commas."),
          D("  (e.g. 'trading-strategies, market-analysis')"),
          "",
          C("  Type allowed skills (or Enter for all):"),
          "",
        ]);
        break;

      case "review": {
        const name = this._data.name ?? "Unnamed";
        const role = this._data.role ?? "";
        const emoji = this._data.emoji ?? "🦉";
        const challenge = CHALLENGE_OPTIONS[parseInt(this._data.challengeLevel ?? "2") - 1] ?? "medium";
        const verbosity = VERBOSITY_OPTIONS[parseInt(this._data.verbosity ?? "2") - 1] ?? "balanced";
        const expertise = (this._data.expertise ?? []).join(", ") || "(none)";
        const allowedTools = (this._data.allowedTools ?? []).join(", ") || "all";
        const deniedTools = (this._data.deniedTools ?? []).join(", ") || "none";
        const constraints = (this._data.capabilityConstraints ?? []).join("; ") || "none";
        const model = this._data.model ?? "(default)";
        const skills = (this._data.skills ?? []).join(", ") || "all";

        ui.printLines([
          "",
          YB("Review — specialized_owl.md"),
          sep(),
          `${emoji} ${YB(name)} — ${W(role)}`,
          sep(),
          D("Personality"),
          `  Challenge: ${W(challenge.split(" — ")[0])}`,
          `  Verbosity: ${W(verbosity.split(" — ")[0])}`,
          `  Tone: ${W(this._data.tone ?? "neutral")}`,
          sep(),
          D("Expertise"),
          `  ${W(expertise)}`,
          sep(),
          D("Permissions"),
          `  Allowed: ${W(allowedTools)}`,
          `  Denied: ${W(deniedTools)}`,
          `  Constraints: ${W(constraints)}`,
          sep(),
          D("Model"),
          `  ${W(model)}`,
          sep(),
          D("Skills"),
          `  ${W(skills)}`,
          sep(),
          C("  Type 'yes' to create the owl, 'no' to cancel:"),
          "",
        ]);
        break;
      }
    }
  }

  private async _handle(input: string, ui: TerminalRenderer): Promise<boolean> {
    switch (this._step) {
      case "welcome":
        if (input.trim()) {
          this._step = "name";
        }
        this._showStep(ui);
        return false;

      case "name":
        if (!input.trim()) {
          ui.printInfo(R("  Name cannot be empty. Try again:"));
          return false;
        }
        this._data.name = input.trim();
        this._step = "role";
        this._showStep(ui);
        return false;

      case "role":
        if (!input.trim()) {
          ui.printInfo(R("  Role cannot be empty. Try again:"));
          return false;
        }
        this._data.role = input.trim();
        this._step = "emoji";
        this._showStep(ui);
        return false;

      case "emoji":
        this._data.emoji = input.trim() || "🦉";
        this._step = "challenge_level";
        this._showStep(ui);
        return false;

      case "challenge_level": {
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > 4) {
          ui.printInfo(R("  Type a number 1-4. Try again:"));
          return false;
        }
        const levels = ["low", "medium", "high", "relentless"];
        this._data.challengeLevel = levels[n - 1];
        this._step = "verbosity";
        this._showStep(ui);
        return false;
      }

      case "verbosity": {
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > 3) {
          ui.printInfo(R("  Type a number 1-3. Try again:"));
          return false;
        }
        const levels = ["concise", "balanced", "verbose"];
        this._data.verbosity = levels[n - 1];
        this._step = "tone";
        this._showStep(ui);
        return false;
      }

      case "tone":
        this._data.tone = input.trim() || "neutral";
        this._step = "expertise";
        this._showStep(ui);
        return false;

      case "expertise":
        this._data.expertise = input
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        this._step = "allowed_tools";
        this._showStep(ui);
        return false;

      case "allowed_tools":
        this._data.allowedTools = input
          .split(",")
          .map((s) => s.trim().toLowerCase())
          .filter(Boolean);
        this._step = "denied_tools";
        this._showStep(ui);
        return false;

      case "denied_tools":
        this._data.deniedTools = input
          .split(",")
          .map((s) => s.trim().toLowerCase())
          .filter(Boolean);
        this._step = "capability_constraints";
        this._showStep(ui);
        return false;

      case "capability_constraints":
        this._data.capabilityConstraints = input
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        this._step = "model_provider";
        this._showStep(ui);
        return false;

      case "model_provider": {
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > 4) {
          ui.printInfo(R("  Type a number 1-4. Try again:"));
          return false;
        }
        const providers = ["anthropic", "openai", "ollama", "default"];
        this._data.provider = providers[n - 1];
        this._step = "model_name";
        this._showStep(ui);
        return false;
      }

      case "model_name":
        if (input.trim()) {
          this._data.model = input.trim();
        }
        this._step = "model_tokens";
        this._showStep(ui);
        return false;

      case "model_tokens":
        if (input.trim()) {
          const tokens = parseInt(input, 10);
          if (!isNaN(tokens) && tokens > 0) {
            this._data.maxTokens = tokens;
          }
        }
        this._step = "skills";
        this._showStep(ui);
        return false;

      case "skills":
        this._data.skills = input
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        this._step = "review";
        this._showStep(ui);
        return false;

      case "review": {
        const answer = input.toLowerCase();
        if (answer === "yes" || answer === "y") {
          try {
            await this._createSpecFile();
            ui.printLines([
              "",
              G("✓ Owl created successfully!"),
              sep(),
              D("  Files created:"),
              `  • workspace/owls/${this._data.name}/specialized_owl.md`,
              `  • workspace/owls/${this._data.name}/credentials/secrets.md`,
              "",
              D("  Use ") + C(`/specialization list`) + D(" to see your owls."),
              D("  Use ") + C(`@${this._data.name} hello`) + D(" to chat with this owl."),
              "",
            ]);
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            ui.printLines([
              "",
              R("✕ Failed to create owl:"),
              R(`  ${msg}`),
              "",
            ]);
          }
          return true;
        }
        if (answer === "no" || answer === "n") {
          ui.printLines(["", D("  Owl creation cancelled."), ""]);
          return true;
        }
        ui.printInfo(R("  Type 'yes' to create or 'no' to cancel:"));
        return false;
      }

      default:
        return false;
    }
  }

  private async _createSpecFile(): Promise<void> {
    const d = this._data;
    const owlName = d.name!;
    const folderName = owlName.replace(/[^a-zA-Z0-9-_]/g, "");

    const challengeMap: Record<string, string> = {
      low: "low",
      medium: "medium",
      high: "high",
      relentless: "relentless",
    };
    const verbosityMap: Record<string, string> = {
      concise: "concise",
      balanced: "balanced",
      verbose: "verbose",
    };

    const content = `# ${owlName}

## Identity
name: ${owlName}
role: ${d.role}
emoji: ${d.emoji || "🦉"}

## Personality
challengeLevel: ${challengeMap[d.challengeLevel ?? "medium"] ?? "medium"}
verbosity: ${verbosityMap[d.verbosity ?? "balanced"] ?? "balanced"}
tone: ${d.tone || "neutral"}

## Expertise
domains:
${(d.expertise ?? []).map((e) => `  - ${e}`).join("\n")}

## Model Config
provider: ${d.provider === "default" ? "" : d.provider || ""}
model: ${d.model || ""}
maxTokens: ${d.maxTokens ?? ""}

## Permissions
allowedTools:
${(d.allowedTools ?? []).map((t) => `  - ${t}`).join("\n") || "  # all tools"}
deniedTools:
${(d.deniedTools ?? []).map((t) => `  - ${t}`).join("\n") || "  # no restrictions"}
capabilityConstraints:
${(d.capabilityConstraints ?? []).map((c) => `  - "${c}"`).join("\n") || "  # no constraints"}

## Routing Rules
keywords:
${(d.expertise ?? []).map((e) => `  - ${e.toLowerCase()}`).join("\n")}

## Skills
allowed:
${(d.skills ?? []).map((s) => `  - ${s}`).join("\n") || "  # all skills"}
`;

    const basePath = resolve(process.cwd(), "workspace", "owls", folderName);
    const specPath = join(basePath, "specialized_owl.md");
    const credPath = join(basePath, "credentials", "secrets.md");

    if (!existsSync(basePath)) {
      await mkdir(basePath, { recursive: true });
    }

    await writeFile(specPath, content, "utf8");

    const credContent = `# ${owlName} Credentials
# Store API keys and secrets here. This file is gitignored.
# Example:
# API_KEY=your_api_key_here
# SECRET_TOKEN=your_secret_token
`;
    await mkdir(join(basePath, "credentials"), { recursive: true });
    await writeFile(credPath, credContent, "utf8");
  }

  generateSpecFile(): string {
    const d = this._data;
    const challengeMap: Record<string, string> = {
      low: "low",
      medium: "medium",
      high: "high",
      relentless: "relentless",
    };
    const verbosityMap: Record<string, string> = {
      concise: "concise",
      balanced: "balanced",
      verbose: "verbose",
    };

    return `# ${d.name ?? "Unnamed"}

## Identity
name: ${d.name ?? "Unnamed"}
role: ${d.role ?? ""}
emoji: ${d.emoji ?? "🦉"}

## Personality
challengeLevel: ${challengeMap[d.challengeLevel ?? "medium"] ?? "medium"}
verbosity: ${verbosityMap[d.verbosity ?? "balanced"] ?? "balanced"}
tone: ${d.tone || "neutral"}

## Expertise
domains:
${(d.expertise ?? []).map((e) => `  - ${e}`).join("\n")}

## Model Config
provider: ${d.provider === "default" ? "" : d.provider || ""}
model: ${d.model || ""}
maxTokens: ${d.maxTokens ?? ""}

## Permissions
allowedTools:
${(d.allowedTools ?? []).map((t) => `  - ${t}`).join("\n") || "  # all tools"}
deniedTools:
${(d.deniedTools ?? []).map((t) => `  - ${t}`).join("\n") || "  # no restrictions"}
capabilityConstraints:
${(d.capabilityConstraints ?? []).map((c) => `  - "${c}"`).join("\n") || "  # no constraints"}

## Routing Rules
keywords:
${(d.expertise ?? []).map((e) => `  - ${e.toLowerCase()}`).join("\n")}

## Skills
allowed:
${(d.skills ?? []).map((s) => `  - ${s}`).join("\n") || "  # all skills"}
`;
  }
}