import { appendFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { log } from "../../logger.js";
import type { Notifier, NotifierCapabilities, NotifyOptions, NotifyResult } from "../types.js";

interface NativeNotifierLike {
  notify(opts: { title: string; message: string }, cb: (err: Error | null, response?: string) => void): void;
}

export interface NotifierOptions {
  nativeImpl?: NativeNotifierLike;
  systemLogPath?: string | null;
  systemEventEmitter?: (msg: string) => void;
  stderrSink?: (msg: string) => void;
}

export class NotifierImpl implements Notifier {
  private readonly nativeImpl: NativeNotifierLike | null;
  private readonly systemLogPath: string | null;
  private readonly systemEventEmitter?: (msg: string) => void;
  private readonly stderrSink: (msg: string) => void;

  constructor(opts: NotifierOptions = {}) {
    this.nativeImpl = opts.nativeImpl ?? null;
    this.systemLogPath = opts.systemLogPath ?? null;
    this.systemEventEmitter = opts.systemEventEmitter;
    this.stderrSink = opts.stderrSink ?? ((m) => process.stderr.write(m + "\n"));
  }

  capabilities(): NotifierCapabilities {
    return {
      native: this.nativeImpl !== null,
      system: !!(this.systemLogPath || this.systemEventEmitter),
    };
  }

  async notify(opts: NotifyOptions): Promise<NotifyResult> {
    log.tool.debug("notifier.notify: entry", { title: opts.title.slice(0, 60), urgency: opts.urgency });

    if (this.nativeImpl) {
      const ok = await new Promise<boolean>((res) => {
        try {
          this.nativeImpl!.notify({ title: opts.title, message: opts.body }, (err) => {
            res(!err);
          });
        } catch {
          res(false);
        }
      });
      if (ok) return { delivered: true, via: "native" };
    }

    const message = formatPayload(opts);
    let systemOk = false;
    if (this.systemLogPath) {
      try {
        await mkdir(dirname(this.systemLogPath), { recursive: true });
        await appendFile(this.systemLogPath, message + "\n", "utf-8");
        systemOk = true;
      } catch (err) {
        log.tool.warn("notifier: system log write failed", { err: String(err) });
      }
    }
    if (this.systemEventEmitter) {
      try {
        this.systemEventEmitter(message);
        systemOk = true;
      } catch (err) {
        log.tool.warn("notifier: system event emit failed", { err: String(err) });
      }
    }
    if (systemOk) return { delivered: true, via: "system" };

    this.stderrSink(message);
    return { delivered: true, via: "stderr" };
  }
}

function formatPayload(opts: NotifyOptions): string {
  const urgency = opts.urgency ?? "normal";
  const category = opts.category ? ` [${opts.category}]` : "";
  return `[notifier:${urgency}]${category} ${opts.title} — ${opts.body}`;
}
