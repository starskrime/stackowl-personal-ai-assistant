/**
 * StackOwl — Skill Types
 *
 * Type definitions for OpenCLAW-compatible skills.
 * Skills are instructions that teach the LLM how to accomplish tasks.
 */

export interface SkillMetadata {
  /** Skill name (from frontmatter) */
  name: string;
  /** Human-readable description */
  description: string;
  /** OpenCLAW-specific metadata */
  openclaw?: {
    /** OS requirements (darwin, linux, win32) */
    os?: string[];
    /** Required binary executables */
    requires?: {
      bins?: string[];
      anyBins?: string[];
      env?: string[];
      config?: string[];
    };
    /** Primary environment variable for API keys */
    primaryEnv?: string;
    /** Always include this skill */
    always?: boolean;
    /** Emoji for UI display */
    emoji?: string;
    /** Homepage URL */
    homepage?: string;
    /** Installation specs */
    install?: SkillInstall[];
    /** Skill key for config */
    skillKey?: string;
  };
  /** User-invocable via slash command */
  "user-invocable"?: boolean;
  /** Disable model invocation */
  "disable-model-invocation"?: boolean;
  /** Command dispatch mode */
  "command-dispatch"?: "tool";
  /** Tool to invoke for command dispatch */
  "command-tool"?: string;
  /** Command argument mode */
  "command-arg-mode"?: "raw";
}

export interface SkillInstall {
  id: string;
  kind: "brew" | "node" | "go" | "download";
  formula?: string;
  bins?: string[];
  label?: string;
  url?: string;
  archive?: "tar.gz" | "tar.bz2" | "zip";
  extract?: boolean;
  stripComponents?: number;
  targetDir?: string;
  os?: string[];
}

// ─── Skill Execution Tracking ─────────────────────────────────────

export interface SkillUsageStats {
  /** Total number of times this skill was selected for injection */
  selectionCount: number;
  /** Total number of times execution completed successfully */
  successCount: number;
  /** Total number of times execution failed or was abandoned */
  failureCount: number;
  /** Average execution duration in milliseconds */
  avgDurationMs: number;
  /** ISO timestamp of last use */
  lastUsedAt: string | null;
  /** Rolling success rate: successCount / (successCount + failureCount) */
  successRate: number;
}

// ─── Skill Composition ────────────────────────────────────────────

export interface SkillDependency {
  /** Name of the skill this depends on */
  skillName: string;
  /** Execution order relative to the parent skill */
  order: "before" | "after" | "parallel";
  /** Whether this dependency is required or optional */
  required: boolean;
}

export interface SkillComposition {
  /** Skills this skill depends on */
  dependencies: SkillDependency[];
  /** Skills that should chain after this one */
  chains?: string[];
  /** Whether this is a composite skill (wraps others) */
  isComposite: boolean;
}

// ─── Core Skill Interface ─────────────────────────────────────────

export interface Skill {
  /** Unique identifier */
  name: string;
  /** Human-readable description */
  description: string;
  /** The instructions for the LLM */
  instructions: string;
  /** Parsed metadata */
  metadata: SkillMetadata;
  /** Source file path */
  sourcePath: string;
  /** Whether the skill is currently enabled */
  enabled: boolean;
  /** Skill-specific configuration */
  config?: Record<string, unknown>;
  /** Required environment variables */
  requiredEnv?: string[];
  /** Required binaries */
  requiredBins?: string[];
  /** Usage statistics — populated by SkillTracker */
  usage?: SkillUsageStats;
  /** Composition metadata — skill dependencies and chaining */
  composition?: SkillComposition;
  /** Structured execution parameters (from frontmatter). */
  parameters?: Record<string, SkillParameter>;
  /** Structured execution steps (from frontmatter). */
  steps?: SkillStep[];
}

// ─── Structured Skill Execution ──────────────────────────────────

export interface SkillParameter {
  type: "string" | "number" | "boolean";
  description: string;
  required?: boolean;
  default?: unknown;
}

export interface SkillStep {
  id: string;
  /** Tool to call. Mutually exclusive with type: 'llm'. */
  tool?: string;
  /** Step type — defaults to 'tool' when tool is set. */
  type?: "tool" | "llm";
  /** Tool arguments with {{param}} template interpolation. */
  args?: Record<string, unknown>;
  /** LLM prompt for type: 'llm' steps. Supports {{param}} and {{stepId.output}}. */
  prompt?: string;
  /** Step IDs this depends on — controls parallel scheduling. */
  depends_on?: string[];
  /** References to previous step outputs: ['step_id.output']. */
  inputs?: string[];
  /** Step to jump to on failure. */
  on_failure?: string;
  /** Per-step timeout in ms. Default: 30000. */
  timeout_ms?: number;
  /** If true, failure doesn't fail the skill. */
  optional?: boolean;
}

export type SkillStepStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "skipped";

export interface SkillStepResult {
  stepId: string;
  status: SkillStepStatus;
  output?: string;
  error?: string;
  durationMs: number;
}

export interface SkillExecutionResult {
  skillName: string;
  status: "success" | "failed";
  stepResults: SkillStepResult[];
  /** Final output text (from last LLM step or concatenated tool outputs). */
  finalOutput: string;
  totalDurationMs: number;
  /** Extracted parameters used. */
  parameters: Record<string, unknown>;
}

/** Type guard: does this skill have structured execution steps? */
export function isStructuredSkill(skill: Skill): boolean {
  return Array.isArray(skill.steps) && skill.steps.length > 0;
}

export interface SkillFilter {
  /** Filter by OS */
  os?: NodeJS.Platform;
  /** Filter by available binaries */
  bins?: string[];
  /** Filter by environment variables */
  env?: Record<string, string>;
  /** Filter by config */
  config?: Record<string, unknown>;
}

export interface SkillLoadOptions {
  /** Directories to load skills from */
  directories: string[];
  /** Whether to watch for changes */
  watch?: boolean;
  /** Debounce ms for file watching */
  watchDebounceMs?: number;
}
