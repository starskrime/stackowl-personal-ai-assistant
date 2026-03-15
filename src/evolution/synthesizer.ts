/**
 * StackOwl — Tool Synthesizer
 *
 * Two paths for handling capability gaps:
 *
 *   PRIMARY  — generateSkillMd(): generates a SKILL.md file that teaches the LLM
 *              to accomplish the task using existing tools (shell, files, web).
 *              Safe, auditable, no compilation step. Preferred for everything.
 *
 *   FALLBACK — designSpec() + implement(): TypeScript code generation for tasks that
 *              genuinely cannot be expressed as shell-level instructions.
 *              Kept as escape hatch; not used by default.
 */

import { writeFile, mkdir } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { CapabilityGap } from "./detector.js";
import { SkillCritic } from "../skills/critic.js";
import { SkillParser } from "../skills/parser.js";
import { log } from "../logger.js";

const MAX_SYNTHESIS_RETRIES = 3;
const MIN_QUALITY_THRESHOLD = 0.6;
const TARGET_QUALITY_THRESHOLD = 0.75;

const __dirname = dirname(fileURLToPath(import.meta.url));
export const SYNTHESIZED_DIR = join(__dirname, "../tools/synthesized");

// ─── Skill Synthesis (primary path) ──────────────────────────────

export interface SkillSynthesisResult {
  skillName: string;
  description: string;
  filePath: string;
  content: string;
}

// ─── Types ───────────────────────────────────────────────────────

export interface ToolParameter {
  name: string;
  type: string;
  description: string;
  required: boolean;
}

export interface ToolProposal {
  toolName: string;
  description: string;
  parameters: ToolParameter[];
  rationale: string;
  dependencies: string[];
  safetyNote: string;
  filePath: string;
  owlName: string;
  owlEmoji: string;
}

// ─── Tool Name Normalization ─────────────────────────────────────
// Ensures tool names are generic and reusable, not request-specific.
// "send_email_via_agentmail" → "email_send"
// "fetch_weather_from_openweather" → "weather_fetch"

const SERVICE_SPECIFIC_PATTERNS = [
  /(?:_?via_?\w+)$/i,           // _via_agentmail, _via_slack
  /(?:_?from_?\w+)$/i,          // _from_openweather, _from_api
  /(?:_?using_?\w+)$/i,         // _using_curl, _using_puppeteer
  /(?:_?with_?\w+)$/i,          // _with_selenium
  /(?:_?on_?\w+)$/i,            // _on_telegram (but not "on" alone)
  /(?:_?through_?\w+)$/i,       // _through_api
];

function normalizeToolName(rawName: string): string {
  let name = rawName.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');

  // Strip service-specific suffixes
  for (const pattern of SERVICE_SPECIFIC_PATTERNS) {
    name = name.replace(pattern, '');
  }

  // Remove consecutive underscores and trailing underscores
  name = name.replace(/_+/g, '_').replace(/^_|_$/g, '');

  // If name is too short after cleanup, keep original
  if (name.length < 3) return rawName.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');

  return name;
}

// ─── Synthesizer ─────────────────────────────────────────────────

export class ToolSynthesizer {
  /**
   * PRIMARY PATH: Generate a SKILL.md from a capability gap.
   *
   * Skills teach the LLM HOW to accomplish a task using existing tools
   * (run_shell_command, read_file, write_file, web_crawl, etc.).
   * No TypeScript compilation, no dynamic import, no npm install.
   *
   * Returns the path to the written SKILL.md and its content.
   */
  async generateSkillMd(
    gap: CapabilityGap,
    provider: ModelProvider,
    _owl: OwlInstance,
    config: StackOwlConfig,
    skillsDir: string,
    toolDescriptions?: string[],
    synthesisModel?: string,
  ): Promise<SkillSynthesisResult> {
    const platform = process.platform;

    // Build tool list — use full registry if available, otherwise defaults
    const toolList = toolDescriptions && toolDescriptions.length > 0
      ? toolDescriptions.map(t => `  - ${t}`).join("\n")
      : `  - run_shell_command(command): runs any shell command and returns output\n` +
        `  - read_file(path): reads a file from disk\n` +
        `  - write_file(path, content): writes content to a file\n` +
        `  - edit_file(path, old_text, new_text): edit a file by replacing text\n` +
        `  - web_crawl(url): fetches a URL and returns text content\n` +
        `  - google_search(query): search Google and return results\n` +
        `  - scrapling_fetch(url, mode): anti-bot web scraping (modes: basic, stealth, dynamic)\n` +
        `  - computer_use(action, ...): desktop automation — mouse, keyboard, screenshots, app control\n` +
        `  - take_screenshot(): capture the screen\n` +
        `  - send_file(path, caption): send a file to the user`;

    const prompt =
      `You are writing a SKILL.md for an AI assistant called StackOwl.\n` +
      `StackOwl runs locally on ${platform} and has access to these tools:\n` +
      `${toolList}\n\n` +
      `The user tried to do: "${gap.userRequest}"\n\n` +
      `Write a SKILL.md that teaches the LLM how to accomplish this GENERAL CAPABILITY using the tools above.\n` +
      `CRITICAL: The skill must be GENERIC and REUSABLE — it describes a CAPABILITY, not a specific task.\n` +
      `- The skill name must be a SHORT GENERIC CAPABILITY name — 1 or 2 words max, like a tool name.\n` +
      `  GOOD names: email, screenshot, phone_call, weather, clipboard, file_convert, web_search\n` +
      `  BAD names: send_email_to_john, capture_google_screenshot, fetch_weather_from_api, summarize_bbc_article\n` +
      `- DO NOT include specific email addresses, URLs, names, services, or user-specific details anywhere.\n` +
      `- The skill describes a REUSABLE CAPABILITY that works for ANY instance of this task type.\n` +
      `- Examples in the skill must use placeholder values like "recipient@example.com", not real addresses.\n\n` +
      `Use concrete, step-by-step instructions. Include the exact tool calls and shell commands to use.\n\n` +
      `Output ONLY valid SKILL.md content in this exact format:\n` +
      `---\n` +
      `name: short_generic_name\n` +
      `description: one sentence describing what this skill does\n` +
      `openclaw:\n` +
      `  emoji: 🔧\n` +
      `---\n\n` +
      `# How to [accomplish the task]\n\n` +
      `[Step-by-step instructions for the LLM. Be concrete. Include exact tool calls.]\n\n` +
      `## Examples\n` +
      `[1-2 concrete examples with actual tool invocations]\n\n` +
      `## Error Handling\n` +
      `[What to do if a tool fails — include fallback strategies]\n\n` +
      `Rules:\n` +
      `- name MUST be 1-2 words, snake_case, describing the CAPABILITY (like a tool name).\n` +
      `  GOOD: email, screenshot, phone_call, weather, clipboard, notification\n` +
      `  BAD:  send_email_via_agentmail, take_and_send_screenshot, find_top_ai_news\n` +
      `- Instructions must be actionable using the tools listed above\n` +
      `- PREFER specialized tools over shell commands (e.g. use computer_use for desktop, scrapling_fetch for blocked sites)\n` +
      `- Include error handling and fallback steps\n` +
      `- No TypeScript, no code generation — pure natural language instructions\n` +
      `- Output ONLY the SKILL.md content, nothing else`;

    const model = synthesisModel ?? config.synthesis?.model ?? config.defaultModel;
    const response = await provider.chat(
      [{ role: "user", content: prompt }],
      model,
    );

    let content = response.content.trim();

    // ── Post-synthesis quality gate (Self-Refine loop, up to MAX_SYNTHESIS_RETRIES) ──
    const critic = new SkillCritic(provider);
    const parser = new SkillParser();
    let bestContent = content;
    let bestScore = 0;

    try {
      for (let attempt = 0; attempt < MAX_SYNTHESIS_RETRIES; attempt++) {
        const currentContent = attempt === 0 ? content : bestContent;
        const provisionalSkill = parser.parseContent(currentContent, "provisional");
        const critique = await critic.critique(provisionalSkill);
        const score = critique.overallScore;

        log.engine.info(
          `[SkillSynthesis] Attempt ${attempt + 1}/${MAX_SYNTHESIS_RETRIES}: score=${score.toFixed(2)} (target=${TARGET_QUALITY_THRESHOLD})`,
        );

        // Track best version seen
        if (score > bestScore) {
          bestScore = score;
          bestContent = currentContent;
        }

        // Good enough — accept and stop
        if (score >= TARGET_QUALITY_THRESHOLD) {
          content = bestContent;
          break;
        }

        // Below minimum — retry with critique feedback
        if (score < MIN_QUALITY_THRESHOLD && attempt < MAX_SYNTHESIS_RETRIES - 1) {
          const retryPrompt =
            prompt +
            `\n\n[QUALITY FEEDBACK — attempt ${attempt + 1} scored ${score.toFixed(2)}/1.0]\n` +
            `You MUST address ALL of the following issues:\n` +
            `- Name clarity (${critique.nameClarityScore.score.toFixed(2)}): ${critique.nameClarityScore.feedback}\n` +
            `- Instructions (${critique.instructionClarityScore.score.toFixed(2)}): ${critique.instructionClarityScore.feedback}\n` +
            `- Trigger precision (${critique.triggerPrecisionScore.score.toFixed(2)}): ${critique.triggerPrecisionScore.feedback}\n` +
            `Rewrite the SKILL.md now, fixing all issues above.`;

          const retryResponse = await provider.chat(
            [{ role: "user", content: retryPrompt }],
            model,
          );
          const retryContent = retryResponse.content.trim();
          if (retryContent.includes("---") && retryContent.includes("name:")) {
            bestContent = retryContent;
          }
          continue;
        }

        // Between min and target — accept best so far
        content = bestContent;
        break;
      }
    } catch (err) {
      log.engine.warn(
        `[SkillSynthesis] Critique loop failed, using best available content: ${err instanceof Error ? err.message : String(err)}`,
      );
      content = bestContent;
    }

    // Extract skill name from frontmatter and normalize to generic form
    const nameMatch = content.match(/^name:\s*(\S+)/m);
    const rawName = nameMatch ? nameMatch[1] : "synthesized_skill";
    const skillName = normalizeToolName(rawName);

    // Update the frontmatter if name was normalized
    if (skillName !== rawName && nameMatch) {
      content = content.replace(/^(name:\s*)\S+/m, `$1${skillName}`);
    }

    // Write to skills directory
    const skillDir = join(skillsDir, skillName);
    const filePath = join(skillDir, "SKILL.md");
    await mkdir(skillDir, { recursive: true });
    await writeFile(filePath, content, "utf-8");

    return {
      skillName,
      description: gap.userRequest.slice(0, 100),
      filePath,
      content,
    };
  }

  /**
   * Step 1: Design a tool spec from a detected gap.
   * This is what gets shown to the user for approval — no code written yet.
   */
  async designSpec(
    gap: CapabilityGap,
    provider: ModelProvider,
    owl: OwlInstance,
    config: StackOwlConfig,
    synthesisModel?: string,
  ): Promise<ToolProposal> {
    const platform = process.platform; // 'darwin' | 'linux' | 'win32'

    const prompt =
      `You are the self-improvement engine for an AI assistant called StackOwl.\n` +
      `StackOwl runs as a Node.js process on the SAME MACHINE as the user (platform: ${platform}).\n` +
      `It has full access to: child_process (exec/spawn), the filesystem, network, and all system commands.\n\n` +
      `The user made this request: "${gap.userRequest}"\n\n` +
      `Design a tool that DIRECTLY FULFILLS the user's request using system-level capabilities.\n` +
      `CRITICAL RULES:\n` +
      `- The tool must ACTUALLY DO the action (e.g., capture screen, send email, read files).\n` +
      `- Do NOT design a tool that asks the user to do the task themselves.\n` +
      `- Do NOT design a tool that just processes something the user would manually send.\n` +
      `- Use system commands where appropriate:\n` +
      `    - If linux: use Linux commands (scrot, xdotool, notify-send, apt, etc.)\n` +
      `    - If darwin/macOS: use macOS commands (screencapture, osascript, say, etc.)\n` +
      `    - If win32: use Windows commands (PowerShell, etc.)\n` +
      `    - NEVER say "I can't" or "not supported on this platform" — ALWAYS find a way\n\n` +
      `    - When creating tools that might execute in a Docker container environment (like Alpine Linux), ensure they provide fallbacks for commonly missing commands\n` +
      `    - If a platform-specific command is not available in containers (e.g., 'screencapture' on macOS in Alpine), include a Node.js fallback using cross-platform libraries\n\n` +
      `Respond ONLY with valid JSON:\n` +
      `{\n` +
      `  "toolName": "snake_case_tool_name",\n` +
      `  "description": "One sentence: what this tool DOES (active voice, e.g. 'Captures a screenshot of the screen and returns the file path')",\n` +
      `  "parameters": [\n` +
      `    { "name": "param_name", "type": "string|number|boolean", "description": "what it is", "required": true }\n` +
      `  ],\n` +
      `  "rationale": "One sentence: which system command or API this uses and how",\n` +
      `  "dependencies": ["npm-package-name"],\n` +
      `  "safetyNote": "What external systems this touches: filesystem / network / screen / none"\n` +
      `}\n\n` +
      `Additional rules:\n` +
      `- toolName MUST be 1-2 words max, snake_case, describing the CAPABILITY (like a tool name).\n` +
      `  GOOD: email, screenshot, phone_call, weather, clipboard, notification\n` +
      `  BAD: send_email_via_agentmail, fetch_weather_from_openweather, gmail_send_message\n` +
      `- Keep it minimal — solve only what the user asked for\n` +
      `- If no npm packages are needed, set dependencies to []\n` +
      `- Output ONLY the JSON object, no markdown fences`;

    const model = synthesisModel ?? config.synthesis?.model ?? config.defaultModel;
    const response = await provider.chat(
      [{ role: "user", content: prompt }],
      model,
    );

    const spec = this.parseJson(response.content);
    const rawToolName = (spec.toolName as string | undefined) ?? "custom_tool";
    const toolName = normalizeToolName(rawToolName);
    const fileName = `${toolName}.ts`;

    return {
      toolName,
      description:
        (spec.description as string | undefined) ??
        `Tool for: ${gap.userRequest.slice(0, 60)}`,
      parameters: Array.isArray(spec.parameters)
        ? (spec.parameters as ToolParameter[])
        : [],
      rationale: (spec.rationale as string | undefined) ?? gap.description,
      dependencies: Array.isArray(spec.dependencies)
        ? (spec.dependencies as string[])
        : [],
      safetyNote: (spec.safetyNote as string | undefined) ?? "Unknown",
      filePath: join(SYNTHESIZED_DIR, fileName),
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
    };
  }

  /**
   * Step 2: Generate the TypeScript implementation and write it to disk.
   * Only called after user approval.
   */
  async implement(
    proposal: ToolProposal,
    provider: ModelProvider,
    _owl: OwlInstance,
    config: StackOwlConfig,
    previousError?: string,
    synthesisModel?: string,
  ): Promise<string> {
    const schemaProperties = proposal.parameters.reduce(
      (acc, p) => {
        acc[p.name] = { type: p.type, description: p.description };
        return acc;
      },
      {} as Record<string, { type: string; description: string }>,
    );

    const requiredParams = proposal.parameters
      .filter((p) => p.required)
      .map((p) => p.name);
    const schemaStr = JSON.stringify(
      {
        type: "object",
        properties: schemaProperties,
        required: requiredParams,
      },
      null,
      8,
    );

    const pascalName = toPascalCase(proposal.toolName);
    const timestamp = new Date().toISOString();

    const platform = process.platform;

    let prompt =
      `You are implementing a new TypeScript tool for the StackOwl AI assistant.\n` +
      `StackOwl runs as a Node.js process on the SAME MACHINE as the user (platform: ${platform}).\n` +
      `The tool has FULL access to child_process, filesystem, network, and all system commands.\n\n`;

    if (previousError) {
      prompt +=
        `[CRITICAL CORRECTION REQUIRED]\n` +
        `Your previous attempt to build this tool failed with the following error when loading into the Node.js V8 execution engine:\n` +
        `\`\`\`\n${previousError}\n\`\`\`\n` +
        `You MUST fix this error in your rewrite. If it was a missing module (like node:formdata), use a different native approach (like native Node fetch) or add the correct npm package to your imports.\n\n`;
    }

    prompt +=
      `Tool spec:\n` +
      `- Name: ${proposal.toolName}\n` +
      `- Description: ${proposal.description}\n` +
      `- Parameters: ${JSON.stringify(proposal.parameters, null, 2)}\n` +
      `- Dependencies: ${proposal.dependencies.length > 0 ? proposal.dependencies.join(", ") : "none (Node.js built-ins only)"}\n` +
      `- Safety: ${proposal.safetyNote}\n` +
      `- How to implement: ${proposal.rationale}\n\n` +
      `CRITICAL: The execute() function must ACTIVELY PERFORM the action.\n` +
      `Do NOT write code that just tells the user what to do manually.\n` +
      `Do NOT write code that waits for the user to upload something.\n` +
      `Examples of correct approach (adapt to current platform ${platform}):\n` +
      `  - "take screenshot" → linux: exec('scrot /tmp/shot.png'), macOS: exec('screencapture /tmp/shot.png')\n` +
      `  - "open browser" → linux: exec('xdg-open https://example.com'), macOS: exec('open https://example.com')\n` +
      `  - "read clipboard" → linux: exec('xclip -selection clipboard -o'), macOS: exec('pbpaste')\n\n` +
      `CRITICAL: When implementing tools that might execute in Docker containers like Alpine Linux, please consider:\n` +
      `  - If your tool uses platform-specific commands (like screencapture on macOS) that might not be available in containers, include fallback implementations.\n` +
      `  - For example: if using screencapture on macOS but potentially running in a container, implement a fallback using Node.js libraries or cross-platform alternatives.\n` +
      `  - When container execution is possible, use the Node.js file system and child_process directly instead of assuming native OS commands are available.\n` +
      `  - Implement try-catch blocks and proper error handling for cross-platform compatibility.\n\n` +
      `RECOMMENDED APPROACHES TO AVOID CONTAINER FAILURES:\n` +
      `  - For screenshot capability: Include Node.js screenshot libraries as fallbacks when system commands are unavailable\n` +
      `  - Use cross-platform libraries (such as node-screenshot) instead of system-specific commands\n` +
      `  - Implement platform detection within the code to adapt execution strategies\n` +
      `  - Include error messages that explain what to do when capabilities are not available in the environment\n\n` +
      `Write the COMPLETE TypeScript file. Use EXACTLY this structure:\n\n` +
      `// AUTO-GENERATED by ${proposal.owlEmoji} ${proposal.owlName} | ${timestamp}\n` +
      `// Reason: ${proposal.rationale}\n` +
      `import type { ToolImplementation, ToolContext } from '../registry.js';\n` +
      `// (add any other imports here)\n\n` +
      `const ${pascalName}Tool: ToolImplementation = {\n` +
      `    definition: {\n` +
      `        name: '${proposal.toolName}',\n` +
      `        description: '${proposal.description}',\n` +
      `        parameters: ${schemaStr},\n` +
      `    },\n` +
      `    async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {\n` +
      `        // implementation\n` +
      `    },\n` +
      `};\n\n` +
      `export default ${pascalName}Tool;\n\n` +
      `Implementation rules:\n` +
      `- Use 'node:' prefix for built-ins (node:fs/promises, node:path, node:child_process, etc.)\n` +
      `- For shell commands: import { promisify } from 'node:util'; import { exec } from 'node:child_process'; const execAsync = promisify(exec);\n` +
      `- Always return a descriptive string of what was done (e.g., "Screenshot saved to /tmp/shot.png")\n` +
      `- Throw descriptive Error objects on failure (include actual error message)\n` +
      `- Only export the default — no named exports\n` +
      `- If the tool might run in a container environment, provide fallback implementations for platform-specific commands\n` +
      `- Include proper error detection and recovery mechanisms\n` +
      `- Implement cross-platform detection to adapt behavior at runtime\n` +
      `- Output ONLY the TypeScript file content, no explanation, no markdown fences`;

    const implModel = synthesisModel ?? config.synthesis?.model ?? config.defaultModel;
    const response = await provider.chat(
      [{ role: "user", content: prompt }],
      implModel,
    );

    // Extract raw code — strip markdown fences if present
    let code = response.content.trim();
    const fenceMatch = code.match(/```(?:typescript|ts)?\n([\s\S]+?)```/);
    if (fenceMatch) {
      code = fenceMatch[1].trim();
    }

    // Write to synthesized directory
    await mkdir(SYNTHESIZED_DIR, { recursive: true });
    await writeFile(proposal.filePath, code, "utf-8");

    return proposal.filePath;
  }

  private parseJson(raw: string): Record<string, unknown> {
    let str = raw.trim();
    const fenceMatch = str.match(/```(?:json)?\n?([\s\S]+?)```/);
    if (fenceMatch) str = fenceMatch[1].trim();
    // Find first { ... } block
    const start = str.indexOf("{");
    const end = str.lastIndexOf("}");
    if (start !== -1 && end !== -1) str = str.slice(start, end + 1);
    try {
      return JSON.parse(str);
    } catch {
      return {};
    }
  }
}

function toPascalCase(str: string): string {
  return str
    .split("_")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("");
}
