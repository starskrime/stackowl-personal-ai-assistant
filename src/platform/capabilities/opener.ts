import { spawn } from "node:child_process";
import { platform as osPlatform } from "node:os";
import { log } from "../../logger.js";
import type { Opener } from "../types.js";

export interface OpenerOptions {
  dryRun?: boolean;
}

export class OpenerImpl implements Opener {
  private linuxOpener: string | null = null;
  private readonly dryRun: boolean;

  constructor(opts: OpenerOptions = {}) {
    this.dryRun = opts.dryRun ?? false;
  }

  async open(target: string): Promise<{ launched: boolean; via: string }> {
    const p = osPlatform();
    if (p === "darwin") {
      return this.launch("open", [target], "open");
    }
    if (p === "win32") {
      return this.launch("cmd.exe", ["/c", "start", "", target], "start");
    }
    if (!this.linuxOpener) {
      this.linuxOpener = await this.detectLinuxOpener();
    }
    if (!this.linuxOpener) {
      return { launched: false, via: "none" };
    }
    return this.launch(this.linuxOpener, [target], this.linuxOpener);
  }

  private async launch(bin: string, args: string[], via: string): Promise<{ launched: boolean; via: string }> {
    if (this.dryRun) {
      return { launched: true, via };
    }
    return new Promise((resolveResult) => {
      try {
        const child = spawn(bin, args, { detached: true, stdio: "ignore" });
        child.on("error", (err) => {
          log.tool.warn("opener.launch: spawn error", { bin, err: String(err) });
          resolveResult({ launched: false, via });
        });
        child.unref();
        resolveResult({ launched: true, via });
      } catch (err) {
        log.tool.warn("opener.launch: throw", { bin, err: String(err) });
        resolveResult({ launched: false, via });
      }
    });
  }

  private async detectLinuxOpener(): Promise<string | null> {
    const candidates = ["xdg-open", "gnome-open", "kde-open"];
    for (const c of candidates) {
      const ok = await new Promise<boolean>((res) => {
        const probe = spawn("which", [c], { stdio: "ignore" });
        probe.on("error", () => res(false));
        probe.on("close", (code) => res(code === 0));
      });
      if (ok) return c;
    }
    return null;
  }
}
