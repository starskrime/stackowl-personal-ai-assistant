import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import { hash } from "../utils.js";

export class TemporalAwarenessLayer implements ContextLayer {
  name = "TemporalAwarenessLayer";
  priority = 60;
  maxTokens = 200;
  produces = ["temporal"];
  dependsOn = [];
  shouldFire(_t: TriageSignals): boolean { return true; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    const hourBucket = Math.floor(Date.now() / 3_600_000);
    return hash(t.effectiveUserId + hourBucket);
  }

  async build(_req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const now = new Date();
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return `<temporal>\nCurrent time: ${now.toLocaleString("en-US", { timeZone: tz, dateStyle: "full", timeStyle: "short" })}\nTimezone: ${tz}\n</temporal>`;
  }
}

export class ChannelFormatHintLayer implements ContextLayer {
  name = "ChannelFormatHintLayer";
  priority = 65;
  maxTokens = 100;
  produces = ["channel_hint"];
  dependsOn = [];
  shouldFire(_t: TriageSignals): boolean { return true; }
  getCacheKey(req: ContextRequest, _t: TriageSignals): string | null {
    return hash(req.channelId ?? "default");
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const hints: Record<string, string> = {
      telegram: "Format for Telegram: use short paragraphs, bold with **asterisks**, code in backticks. Max 4096 chars per message.",
      slack: "Format for Slack: use mrkdwn, *bold*, `code`. Keep messages scannable.",
      cli: "Format for CLI: plain text, no markdown rendering.",
    };
    const hint = hints[req.channelId ?? ""] ?? "";
    return hint ? `<channel_format>${hint}</channel_format>` : "";
  }
}

export class ModeDirectiveLayer implements ContextLayer {
  name = "ModeDirectiveLayer";
  priority = 70;
  maxTokens = 200;
  produces = ["mode"];
  dependsOn = [];
  shouldFire(_t: TriageSignals): boolean { return true; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.effectiveUserId + String(t.hasActiveItems));
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const mode = (req.session as any).mode as string | undefined;
    if (!mode) return "";
    return `<mode_directive>${mode}</mode_directive>`;
  }
}

export class SocraticModeLayer implements ContextLayer {
  name = "SocraticModeLayer";
  priority = 75;
  maxTokens = 200;
  produces = ["socratic"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(_t: TriageSignals): boolean {
    return true; // build() returns "" when socratic not enabled in session
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const socratic = (req.session as any).socraticMode as boolean | undefined;
    if (!socratic) return "";
    return "<socratic_mode>Guide the user to discover answers through questions rather than stating them directly.</socratic_mode>";
  }
}
