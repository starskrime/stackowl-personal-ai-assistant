/**
 * StackOwl — Element 7 T19 — Safari JXA driver
 *
 * Drives the user's *live* Safari window via osascript -l JavaScript (JXA).
 * The unified `live_browser` tool routes Safari-frontmost actions here.
 *
 * JXA gives us scriptable access to Application('Safari') without needing
 * Safari's Develop menu enabled or remote debugging set up — the trade-off
 * is no DOM-tree walking, so click/fill go through `do JavaScript` against
 * the front document.
 *
 * Apple's Automation permission gate applies the first time osascript
 * touches Safari; if the user denies it, runner calls will throw and the
 * caller should surface the permission-grant URL. We do *not* swallow
 * those errors here — the driver intentionally fails loud so the tool can
 * tell the user what to do.
 */
import { exec } from "node:child_process";
import { log } from "../../logger.js";
import { promisify } from "node:util";

const execAsync = promisify(exec);

export type JxaRunner = (script: string) => Promise<string>;

async function defaultJxaRunner(script: string): Promise<string> {
  // osascript -l JavaScript -e '<script>'  — single-quote the script and
  // escape any embedded single quotes the JXA-sanitised way ('\'').
  const safe = script.replace(/'/g, `'\\''`);
  const { stdout } = await execAsync(`osascript -l JavaScript -e '${safe}'`);
  return stdout;
}

/** JXA-string-escape: escape backslashes and single quotes. */
function jxa(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

export interface SafariTab {
  title: string;
  url: string;
}

export class SafariDriver {
  constructor(private readonly runner: JxaRunner = defaultJxaRunner) {}

  async listTabs(): Promise<SafariTab[]> {
    const script = `
      const safari = Application('Safari');
      const tabs = safari.windows[0].tabs;
      const out = [];
      for (let i = 0; i < tabs.length; i++) {
        out.push({ title: tabs[i].name(), url: tabs[i].url() });
      }
      JSON.stringify(out);
    `;
    const raw = await this.runner(script);
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter(
        (t): t is SafariTab =>
          t && typeof t.title === "string" && typeof t.url === "string",
      );
    } catch (err) {
      log.tool.warn('operation failed', err);
      return [];
    }
  }

  async activeTabUrl(): Promise<string | null> {
    const script = `Application('Safari').documents[0].url();`;
    const out = (await this.runner(script)).trim();
    return out.length > 0 ? out : null;
  }

  async activeTabText(): Promise<string> {
    return this.runJS("document.body ? document.body.innerText : ''");
  }

  async navigate(url: string): Promise<void> {
    const script = `Application('Safari').documents[0].url = '${jxa(url)}';`;
    await this.runner(script);
  }

  async runJS(js: string): Promise<string> {
    const script = `Application('Safari').doJavaScript('${jxa(js)}', { in: Application('Safari').documents[0] });`;
    return (await this.runner(script)).trim();
  }

  async click(selector: string): Promise<void> {
    const js = `(function(){const el=document.querySelector('${jxa(selector)}');if(el)el.click();})();`;
    await this.runJS(js);
  }

  async fill(selector: string, value: string): Promise<void> {
    const js =
      `(function(){const el=document.querySelector('${jxa(selector)}');` +
      `if(!el)return;el.value='${jxa(value)}';` +
      `el.dispatchEvent(new Event('input',{bubbles:true}));` +
      `el.dispatchEvent(new Event('change',{bubbles:true}));})();`;
    await this.runJS(js);
  }

  async scroll(deltaPx: number): Promise<void> {
    await this.runJS(`window.scrollBy(0, ${Math.trunc(deltaPx)});`);
  }

  async back(): Promise<void> {
    await this.runJS("window.history.back();");
  }

  async forward(): Promise<void> {
    await this.runJS("window.history.forward();");
  }
}
