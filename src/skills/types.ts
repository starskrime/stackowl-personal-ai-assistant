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
