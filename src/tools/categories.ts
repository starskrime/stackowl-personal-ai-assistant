/**
 * StackOwl — Tool Categories & Permission Gating
 */

export type ToolCategory =
  | "filesystem"
  | "shell"
  | "network"
  | "system"
  | "cognitive"
  | "mcp";

export type ToolPermission = "allowed" | "prompt" | "denied";

export const DEFAULT_PERMISSIONS: Record<ToolCategory, ToolPermission> = {
  filesystem: "allowed",
  shell: "allowed",
  network: "allowed",
  system: "allowed",
  cognitive: "allowed",
  mcp: "allowed",
};
