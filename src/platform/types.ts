import type { PlatformErrorCode } from "./errors.js";

// ─── Paths ─────────────────────────────────────────────────────
export interface Paths {
  tempdir(): string;
  home(): string;
  configDir(appName?: string): string;
  cacheDir(appName?: string): string;
  dataDir(appName?: string): string;
  logDir(appName?: string): string;
  isInside(child: string, root: string): boolean;
}

// ─── Sandbox ───────────────────────────────────────────────────
export interface SandboxPolicy {
  workspaceRoots: string[];
  allowTempdir?: boolean;
  allowExtensions?: string[];
  resolveSymlinks?: boolean;
}

export interface SandboxResult {
  ok: boolean;
  resolvedPath: string;
  reason?: PlatformErrorCode;
  message?: string;
}

export interface Sandbox {
  check(rawPath: string, policy: SandboxPolicy): SandboxResult;
}

// ─── Notifier ──────────────────────────────────────────────────
export interface NotifyOptions {
  title: string;
  body: string;
  urgency?: "low" | "normal" | "critical";
  category?: string;
}

export interface NotifyResult {
  delivered: boolean;
  via: "native" | "system" | "stderr";
  reason?: PlatformErrorCode;
}

export interface NotifierCapabilities {
  native: boolean;
  system: boolean;
}

export interface Notifier {
  notify(opts: NotifyOptions): Promise<NotifyResult>;
  capabilities(): NotifierCapabilities;
}

// ─── Process ───────────────────────────────────────────────────
export interface ProcessInfo {
  pid: number;
  ppid?: number;
  name: string;
  cmd?: string;
  cpu?: number;
  memory?: number;
}

export interface ProcessAPI {
  list(filter?: { name?: string; pid?: number }): Promise<ProcessInfo[]>;
  kill(pid: number, signal?: NodeJS.Signals): Promise<boolean>;
  isAlive(pid: number): boolean;
  currentInfo(): ProcessInfo;
}

// ─── Shell ─────────────────────────────────────────────────────
export interface SpawnOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs?: number;
  inputStdin?: string;
}

export interface SpawnResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  durationMs: number;
  timedOut: boolean;
}

export interface Shell {
  exec(command: string, opts?: SpawnOptions): Promise<SpawnResult>;
}

// ─── Opener ────────────────────────────────────────────────────
export interface Opener {
  open(target: string): Promise<{ launched: boolean; via: string }>;
}

// ─── SystemInfo ────────────────────────────────────────────────
export interface SystemCapabilities {
  hasNotifier: boolean;
  hasOpener: boolean;
  hasDocker: boolean;
  hasGit: boolean;
  hasPython: boolean;
  hasNode: boolean;
  hasRipgrep: boolean;
  hasDockerImagesPulled: { python: boolean; node: boolean };
}

export type PlatformName =
  | "darwin" | "linux" | "win32"
  | "freebsd" | "openbsd" | "sunos" | "aix";

export interface SystemInfo {
  platform: PlatformName;
  arch: string;
  release: string;
  locale: string;
  inContainer: boolean;
  inWSL: boolean;
  capabilities: SystemCapabilities;
}

export interface SystemInfoAPI {
  current(): SystemInfo;
  refresh(): Promise<SystemInfo>;
}

// ─── Top-level Platform ────────────────────────────────────────
export interface Platform {
  readonly paths: Paths;
  readonly sandbox: Sandbox;
  readonly notifier: Notifier;
  readonly process: ProcessAPI;
  readonly shell: Shell;
  readonly opener: Opener;
  readonly systemInfo: SystemInfoAPI;
  initialize(): Promise<void>;
}
