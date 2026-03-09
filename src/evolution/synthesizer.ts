/**
 * StackOwl — Tool Synthesizer
 *
 * Two-step LLM pipeline:
 *   1. designSpec()  — reason about what tool is needed (returns proposal for user approval)
 *   2. implement()   — write the TypeScript implementation to src/tools/synthesized/
 */

import { writeFile, mkdir } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { ModelProvider } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import type { StackOwlConfig } from '../config/loader.js';
import type { CapabilityGap } from './detector.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
export const SYNTHESIZED_DIR = join(__dirname, '../tools/synthesized');

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

// ─── Synthesizer ─────────────────────────────────────────────────

export class ToolSynthesizer {
    /**
     * Step 1: Design a tool spec from a detected gap.
     * This is what gets shown to the user for approval — no code written yet.
     */
    async designSpec(
        gap: CapabilityGap,
        provider: ModelProvider,
        owl: OwlInstance,
        config: StackOwlConfig
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
            `    macOS: screencapture, osascript, say, open\n` +
            `    Linux: scrot, xdotool, notify-send\n` +
            `    Any OS: child_process.exec(), Node.js fs, https, etc.\n\n` +
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
            `- toolName must be snake_case and describe the ACTION (not 'processor' or 'handler')\n` +
            `- Keep it minimal — solve only what the user asked for\n` +
            `- If no npm packages are needed, set dependencies to []\n` +
            `- Output ONLY the JSON object, no markdown fences`;

        const response = await provider.chat(
            [{ role: 'user', content: prompt }],
            config.defaultModel,
        );

        const spec = this.parseJson(response.content);
        const toolName = (spec.toolName as string | undefined) ?? 'custom_tool';
        const fileName = `${toolName}.ts`;

        return {
            toolName,
            description: (spec.description as string | undefined) ?? `Tool for: ${gap.userRequest.slice(0, 60)}`,
            parameters: Array.isArray(spec.parameters) ? (spec.parameters as ToolParameter[]) : [],
            rationale: (spec.rationale as string | undefined) ?? gap.description,
            dependencies: Array.isArray(spec.dependencies) ? (spec.dependencies as string[]) : [],
            safetyNote: (spec.safetyNote as string | undefined) ?? 'Unknown',
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
        previousError?: string
    ): Promise<string> {
        const schemaProperties = proposal.parameters.reduce((acc, p) => {
            acc[p.name] = { type: p.type, description: p.description };
            return acc;
        }, {} as Record<string, { type: string; description: string }>);

        const requiredParams = proposal.parameters.filter(p => p.required).map(p => p.name);
        const schemaStr = JSON.stringify(
            { type: 'object', properties: schemaProperties, required: requiredParams },
            null,
            8
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
            `- Dependencies: ${proposal.dependencies.length > 0 ? proposal.dependencies.join(', ') : 'none (Node.js built-ins only)'}\n` +
            `- Safety: ${proposal.safetyNote}\n` +
            `- How to implement: ${proposal.rationale}\n\n` +
            `CRITICAL: The execute() function must ACTIVELY PERFORM the action.\n` +
            `Do NOT write code that just tells the user what to do manually.\n` +
            `Do NOT write code that waits for the user to upload something.\n` +
            `Examples of correct approach:\n` +
            `  - "take screenshot" → exec('screencapture /tmp/shot.png') then read & return the file path\n` +
            `  - "open browser" → exec('open https://example.com') on macOS\n` +
            `  - "read clipboard" → exec('pbpaste') on macOS\n\n` +
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
            `- Output ONLY the TypeScript file content, no explanation, no markdown fences`;

        const response = await provider.chat(
            [{ role: 'user', content: prompt }],
            config.defaultModel,
        );

        // Extract raw code — strip markdown fences if present
        let code = response.content.trim();
        const fenceMatch = code.match(/```(?:typescript|ts)?\n([\s\S]+?)```/);
        if (fenceMatch) {
            code = fenceMatch[1].trim();
        }

        // Write to synthesized directory
        await mkdir(SYNTHESIZED_DIR, { recursive: true });
        await writeFile(proposal.filePath, code, 'utf-8');

        return proposal.filePath;
    }

    private parseJson(raw: string): Record<string, unknown> {
        let str = raw.trim();
        const fenceMatch = str.match(/```(?:json)?\n?([\s\S]+?)```/);
        if (fenceMatch) str = fenceMatch[1].trim();
        // Find first { ... } block
        const start = str.indexOf('{');
        const end = str.lastIndexOf('}');
        if (start !== -1 && end !== -1) str = str.slice(start, end + 1);
        try {
            return JSON.parse(str);
        } catch {
            return {};
        }
    }
}

function toPascalCase(str: string): string {
    return str.split('_').map(s => s.charAt(0).toUpperCase() + s.slice(1)).join('');
}
