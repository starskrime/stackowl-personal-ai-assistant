import { join } from "node:path";
import { log } from "../logger.js";
import { PathsImpl } from "./capabilities/paths.js";
import { SandboxImpl } from "./capabilities/sandbox.js";
import { NotifierImpl } from "./capabilities/notifier.js";
import { ProcessImpl } from "./capabilities/process.js";
import { ShellImpl } from "./capabilities/shell.js";
import { OpenerImpl } from "./capabilities/opener.js";
import { SystemInfoImpl } from "./capabilities/system-info.js";
import type { Platform } from "./types.js";

export interface RegistryOptions {
  appName?: string;
  notifier?: {
    nativeImpl?: any;
    systemLogPath?: string | null;
    systemEventEmitter?: (msg: string) => void;
  };
}

class PlatformRegistry implements Platform {
  readonly paths: PathsImpl;
  readonly sandbox: SandboxImpl;
  readonly notifier: NotifierImpl;
  readonly process: ProcessImpl;
  readonly shell: ShellImpl;
  readonly opener: OpenerImpl;
  readonly systemInfo: SystemInfoImpl;

  constructor(opts: RegistryOptions = {}) {
    const appName = opts.appName ?? "stackowl";
    this.paths = new PathsImpl(appName);
    this.sandbox = new SandboxImpl(this.paths);
    this.process = new ProcessImpl();
    this.shell = new ShellImpl();
    this.opener = new OpenerImpl();
    this.systemInfo = new SystemInfoImpl();

    const defaultNotifyLog = join(this.paths.logDir(), "notifications.log");
    this.notifier = new NotifierImpl({
      nativeImpl: opts.notifier?.nativeImpl,
      systemLogPath: opts.notifier?.systemLogPath ?? defaultNotifyLog,
      systemEventEmitter: opts.notifier?.systemEventEmitter,
    });
  }

  async initialize(): Promise<void> {
    log.engine.info("[platform] initializing capability probe");
    await this.systemInfo.refresh();
    const info = this.systemInfo.current();
    log.engine.info("[platform] initialized", {
      platform: info.platform,
      arch: info.arch,
      inContainer: info.inContainer,
      inWSL: info.inWSL,
      capabilities: info.capabilities,
    });
  }
}

export function createPlatform(opts: RegistryOptions = {}): Platform {
  return new PlatformRegistry(opts);
}
