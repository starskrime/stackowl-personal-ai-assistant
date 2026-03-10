/**
 * StackOWL — OpenCLAW Compatibility Types
 */

export interface OpenCLAWTool {
  name: string;
  description: string;
  parameters: {
    type: "object";
    properties: Record<
      string,
      {
        type: string;
        description: string;
        required?: boolean;
        enum?: string[];
      }
    >;
    required?: string[];
  };
  /** OpenCLAW group (fs, runtime, web, etc.) */
  group?: string;
  /** Whether this tool requires sandbox */
  requiresSandbox?: boolean;
}

export interface ToolExecutionResult {
  success: boolean;
  output: string;
  error?: string;
  metadata?: Record<string, unknown>;
}

export interface ToolProfile {
  name: string;
  allowedTools: string[];
  deniedTools: string[];
  sandboxMode: "none" | "session" | "always";
}

export interface SandboxConfig {
  enabled: boolean;
  image?: string;
  networkAccess?: boolean;
  maxMemory?: string;
  maxCpu?: number;
}

export interface OpenCLAWConfig {
  tools: {
    allow?: string[];
    deny?: string[];
    profile?: string;
    sandbox?: SandboxConfig;
  };
  channels?: Record<
    string,
    {
      enabled: boolean;
      [key: string]: unknown;
    }
  >;
}
