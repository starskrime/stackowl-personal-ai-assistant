/**
 * StackOwl — Connector Presets
 *
 * Pre-built MCP server configurations for popular DevOps tools.
 * Users select a preset, provide credentials, and StackOwl auto-configures the MCP connection.
 */

import type { ConnectorPreset } from "./types.js";

export const CONNECTOR_PRESETS: ConnectorPreset[] = [
  // ─── Version Control ────────────────────────────────────────
  {
    id: "github",
    name: "GitHub",
    description: "Manage repos, PRs, issues, actions, and code search",
    category: "vcs",
    transport: "stdio",
    package: "@modelcontextprotocol/server-github",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-github"],
    requiredEnv: ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    icon: "🐙",
  },
  {
    id: "gitlab",
    name: "GitLab",
    description: "Manage GitLab repos, merge requests, pipelines",
    category: "vcs",
    transport: "stdio",
    package: "@modelcontextprotocol/server-gitlab",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-gitlab"],
    requiredEnv: ["GITLAB_PERSONAL_ACCESS_TOKEN"],
    optionalEnv: ["GITLAB_API_URL"],
    icon: "🦊",
  },

  // ─── Cloud ──────────────────────────────────────────────────
  {
    id: "aws",
    name: "AWS",
    description: "Manage AWS resources — EC2, S3, Lambda, CloudWatch, etc.",
    category: "cloud",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@anthropic/mcp-server-aws"],
    requiredEnv: ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
    optionalEnv: ["AWS_REGION", "AWS_SESSION_TOKEN"],
    icon: "☁️",
  },
  {
    id: "kubernetes",
    name: "Kubernetes",
    description: "Manage K8s clusters — pods, deployments, services, logs",
    category: "infra",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-kubernetes"],
    requiredEnv: [],
    optionalEnv: ["KUBECONFIG"],
    icon: "☸️",
  },

  // ─── Databases ──────────────────────────────────────────────
  {
    id: "postgres",
    name: "PostgreSQL",
    description: "Query and manage PostgreSQL databases",
    category: "database",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-postgres"],
    requiredEnv: ["POSTGRES_CONNECTION_STRING"],
    icon: "🐘",
  },
  {
    id: "sqlite",
    name: "SQLite",
    description: "Query and manage SQLite databases",
    category: "database",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-sqlite"],
    requiredEnv: ["SQLITE_DB_PATH"],
    icon: "📦",
  },

  // ─── Monitoring ─────────────────────────────────────────────
  {
    id: "sentry",
    name: "Sentry",
    description: "View errors, issues, and performance data from Sentry",
    category: "monitoring",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-sentry"],
    requiredEnv: ["SENTRY_AUTH_TOKEN", "SENTRY_ORG"],
    icon: "🔍",
  },

  // ─── Communication ─────────────────────────────────────────
  {
    id: "slack",
    name: "Slack",
    description: "Read and send Slack messages, manage channels",
    category: "communication",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-slack"],
    requiredEnv: ["SLACK_BOT_TOKEN"],
    optionalEnv: ["SLACK_TEAM_ID"],
    icon: "💬",
  },

  // ─── CI/CD ──────────────────────────────────────────────────
  {
    id: "linear",
    name: "Linear",
    description: "Manage Linear issues, projects, and cycles",
    category: "ci",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-linear"],
    requiredEnv: ["LINEAR_API_KEY"],
    icon: "📋",
  },

  // ─── SSH ────────────────────────────────────────────────────
  {
    id: "ssh",
    name: "SSH Server",
    description: "Connect to remote servers via SSH for debugging and management",
    category: "infra",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@anthropic/mcp-server-ssh"],
    requiredEnv: ["SSH_HOST", "SSH_USER"],
    optionalEnv: ["SSH_KEY_PATH", "SSH_PORT"],
    icon: "🔐",
  },
];

export function getPreset(id: string): ConnectorPreset | undefined {
  return CONNECTOR_PRESETS.find(p => p.id === id);
}

export function listPresets(category?: ConnectorPreset["category"]): ConnectorPreset[] {
  if (category) return CONNECTOR_PRESETS.filter(p => p.category === category);
  return [...CONNECTOR_PRESETS];
}

export function listCategories(): string[] {
  return [...new Set(CONNECTOR_PRESETS.map(p => p.category))];
}
