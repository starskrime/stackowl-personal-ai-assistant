/**
 * StackOwl — Skill Parser
 *
 * Parses OpenCLAW-compatible SKILL.md files.
 */

import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import matter from "gray-matter";
import YAML from "yaml";
import type {
  Skill,
  SkillMetadata,
  SkillParameter,
  SkillStep,
} from "./types.js";

export class SkillParser {
  /**
   * Parse a SKILL.md file and return a Skill object.
   */
  async parse(filePath: string): Promise<Skill> {
    if (!existsSync(filePath)) {
      throw new Error(`Skill file not found: ${filePath}`);
    }

    const raw = await readFile(filePath, "utf-8");
    return this.parseContent(raw, filePath);
  }

  /**
   * Parse SKILL.md content (raw string).
   */
  parseContent(raw: string, sourcePath: string = "unknown"): Skill {
    const { data, content } = matter(raw);

    if (!data.name || typeof data.name !== "string") {
      throw new Error(`SKILL.md missing required "name" field in frontmatter`);
    }

    if (!data.description || typeof data.description !== "string") {
      throw new Error(
        `SKILL.md missing required "description" field in frontmatter`,
      );
    }

    const metadata = this.parseMetadata(data);
    const requiredEnv = this.extractRequiredEnv(metadata);
    const requiredBins = this.extractRequiredBins(metadata);

    const parameters = this.parseParameters(data);
    const steps = this.parseSteps(data);

    return {
      name: data.name,
      description: data.description,
      instructions: content.trim(),
      metadata,
      sourcePath,
      enabled: true,
      requiredEnv,
      requiredBins,
      ...(Object.keys(parameters).length > 0 ? { parameters } : {}),
      ...(steps.length > 0 ? { steps } : {}),
    };
  }

  /**
   * Parse frontmatter data into typed metadata.
   */
  private parseMetadata(data: Record<string, unknown>): SkillMetadata {
    const metadata: SkillMetadata = {
      name: data.name as string,
      description: data.description as string,
    };

    // Parse OpenCLAW metadata
    if (data.metadata) {
      const rawMetadata = data.metadata;
      if (typeof rawMetadata === "string") {
        try {
          metadata.openclaw = JSON.parse(rawMetadata);
        } catch {
          // Try YAML parsing
          try {
            metadata.openclaw = YAML.parse(rawMetadata);
          } catch {
            // Ignore parse errors
          }
        }
      } else if (typeof rawMetadata === "object" && rawMetadata !== null) {
        metadata.openclaw = rawMetadata as SkillMetadata["openclaw"];
      }
    }

    // Parse other optional fields
    if (typeof data["user-invocable"] === "boolean") {
      metadata["user-invocable"] = data["user-invocable"];
    }

    if (typeof data["disable-model-invocation"] === "boolean") {
      metadata["disable-model-invocation"] = data["disable-model-invocation"];
    }

    if (typeof data["command-dispatch"] === "string") {
      metadata["command-dispatch"] = data["command-dispatch"] as "tool";
    }

    if (typeof data["command-tool"] === "string") {
      metadata["command-tool"] = data["command-tool"];
    }

    if (typeof data["command-arg-mode"] === "string") {
      metadata["command-arg-mode"] = data["command-arg-mode"] as "raw";
    }

    return metadata;
  }

  /**
   * Parse structured execution parameters from frontmatter.
   */
  private parseParameters(
    data: Record<string, unknown>,
  ): Record<string, SkillParameter> {
    const params: Record<string, SkillParameter> = {};
    if (!data.parameters || typeof data.parameters !== "object") return params;

    for (const [key, val] of Object.entries(
      data.parameters as Record<string, unknown>,
    )) {
      if (!val || typeof val !== "object") continue;
      const v = val as Record<string, unknown>;
      if (!v.type || !v.description) continue;
      params[key] = {
        type: String(v.type) as SkillParameter["type"],
        description: String(v.description),
        required: v.required !== false,
        ...(v.default !== undefined ? { default: v.default } : {}),
      };
    }
    return params;
  }

  /**
   * Parse structured execution steps from frontmatter.
   */
  private parseSteps(data: Record<string, unknown>): SkillStep[] {
    if (!Array.isArray(data.steps)) return [];

    const stepIds = new Set<string>();
    const steps: SkillStep[] = [];

    for (const raw of data.steps) {
      if (!raw || typeof raw !== "object" || !raw.id) continue;
      const s = raw as Record<string, unknown>;
      const id = String(s.id);
      if (stepIds.has(id)) continue; // skip duplicate ids
      stepIds.add(id);

      const step: SkillStep = { id };

      if (s.tool) {
        step.tool = String(s.tool);
        step.type = "tool";
      } else if (s.type === "llm") {
        step.type = "llm";
      }

      if (s.args && typeof s.args === "object") {
        step.args = s.args as Record<string, unknown>;
      }
      if (s.prompt) step.prompt = String(s.prompt);
      if (Array.isArray(s.depends_on))
        step.depends_on = s.depends_on.map(String);
      if (Array.isArray(s.inputs)) step.inputs = s.inputs.map(String);
      if (s.on_failure) step.on_failure = String(s.on_failure);
      if (typeof s.timeout_ms === "number") step.timeout_ms = s.timeout_ms;
      if (s.optional === true) step.optional = true;

      steps.push(step);
    }

    // Validate on_failure references
    for (const step of steps) {
      if (step.on_failure && !stepIds.has(step.on_failure)) {
        step.on_failure = undefined; // invalid reference, clear it
      }
    }

    return steps;
  }

  /**
   * Extract required environment variables from metadata.
   */
  private extractRequiredEnv(metadata: SkillMetadata): string[] {
    const envVars: string[] = [];

    if (metadata.openclaw?.requires?.env) {
      envVars.push(...metadata.openclaw.requires.env);
    }

    if (metadata.openclaw?.primaryEnv) {
      if (!envVars.includes(metadata.openclaw.primaryEnv)) {
        envVars.push(metadata.openclaw.primaryEnv);
      }
    }

    return envVars;
  }

  /**
   * Extract required binaries from metadata.
   */
  private extractRequiredBins(metadata: SkillMetadata): string[] {
    const bins: string[] = [];

    if (metadata.openclaw?.requires?.bins) {
      bins.push(...metadata.openclaw.requires.bins);
    }

    if (metadata.openclaw?.requires?.anyBins) {
      bins.push(...metadata.openclaw.requires.anyBins);
    }

    return bins;
  }
}

/**
 * Check if a skill's requirements are met.
 */
export function meetsRequirements(
  skill: Skill,
  options: {
    os?: NodeJS.Platform;
    bins?: string[];
    env?: Record<string, string>;
    config?: Record<string, unknown>;
  },
): { satisfied: boolean; missing: string[] } {
  const missing: string[] = [];
  const metadata = skill.metadata;

  // Check OS requirement
  if (metadata.openclaw?.os && metadata.openclaw.os.length > 0) {
    if (!options.os || !metadata.openclaw.os.includes(options.os)) {
      return {
        satisfied: false,
        missing: [`OS ${options.os} not in ${metadata.openclaw.os.join(", ")}`],
      };
    }
  }

  // Check binary requirements
  if (metadata.openclaw?.requires?.bins) {
    const availableBins = options.bins || [];
    for (const bin of metadata.openclaw.requires.bins) {
      if (!availableBins.includes(bin)) {
        missing.push(`binary: ${bin}`);
      }
    }
  }

  // Check environment variable requirements
  if (metadata.openclaw?.requires?.env) {
    const availableEnv = options.env || {};
    for (const envVar of metadata.openclaw.requires.env) {
      if (!availableEnv[envVar] && !process.env[envVar]) {
        missing.push(`env: ${envVar}`);
      }
    }
  }

  // Check config requirements
  if (metadata.openclaw?.requires?.config) {
    const availableConfig = options.config || {};
    for (const configKey of metadata.openclaw.requires.config) {
      if (!availableConfig[configKey]) {
        missing.push(`config: ${configKey}`);
      }
    }
  }

  return {
    satisfied: missing.length === 0,
    missing,
  };
}
