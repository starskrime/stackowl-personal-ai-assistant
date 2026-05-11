export type PlatformErrorCode =
  | "E_OUTSIDE_SANDBOX"
  | "E_EXTENSION_BLOCKED"
  | "E_PATH_INVALID"
  | "E_DOCKER_BYPASS_LOGGED"
  | "E_PLATFORM_UNSUPPORTED"
  | "E_CAPABILITY_MISSING"
  | "E_NOTIFY_NATIVE_FAILED"
  | "E_NOTIFY_SYSTEM_FAILED"
  | "E_SHELL_TIMEOUT"
  | "E_PROCESS_NOT_FOUND";

export class PlatformError extends Error {
  readonly code: PlatformErrorCode;
  readonly cause?: unknown;

  constructor(code: PlatformErrorCode, message: string, cause?: unknown) {
    super(message);
    this.name = "PlatformError";
    this.code = code;
    this.cause = cause;
  }
}
