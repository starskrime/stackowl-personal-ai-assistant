/**
 * /config command — browse and edit every section of stackowl.config.json.
 *
 * Architecture:
 *  - Top-level sections render as a panel with drill-down items.
 *  - Drilling into an object/array opens a new panel (via bridge.openPanel).
 *  - Scalar fields are inline-editable via the Panel EditableSpec.
 *  - Mutations apply in-place so all config consumers see the change immediately.
 *  - saveConfig() validates + persists atomically; on error the mutation is
 *    rolled back and a notice is shown.
 */

import type { CommandHandler, CommandContext } from "../registry.js";
import type { PanelItem } from "../../panels/Panel.js";
import { saveConfig } from "../../../../config/loader.js";
import {
  inferType,
  getAtPath,
  setAtPath,
  isSecretPath,
  maskSecret,
  maskIfSecret,
  displayValue,
  parseScalarInput,
  type ConfigValueType,
} from "./config-schema.js";

// ─── Tier handlers ────────────────────────────────────────────────────────────

const VALID_TIERS = ["low", "mid", "high"] as const;
type Tier = (typeof VALID_TIERS)[number];

function ensureIntelligenceBlock(config: Record<string, unknown>): void {
  if (config.intelligence) return;
  const defaultProvider = (config.defaultProvider as string | undefined) ?? "ollama";
  const defaultModel   = (config.defaultModel   as string | undefined) ?? "llama3.2";
  config.intelligence = {
    tiers: {
      low:  { provider: defaultProvider, model: defaultModel },
      mid:  { provider: defaultProvider, model: defaultModel },
      high: { provider: defaultProvider, model: defaultModel },
    },
    defaults: {
      conversation:   "low",
      parliament:     "low",
      synthesis:      "low",
      evolution:      "low",
      clarification:  "low",
      extraction:     "low",
      episodic:       "low",
      classification: "low",
      summarization:  "low",
    },
  };
}

/** /config tiers — show all three tiers as a drillable panel */
export const handleConfigTiers: CommandHandler = async (ctx) => {
  const gateway  = ctx.getOwlGateway();
  const config   = gateway.getConfig() as unknown as Record<string, unknown>;
  const basePath = gateway.getWorkspacePath();

  ensureIntelligenceBlock(config);
  await saveConfig(basePath, config as unknown as Parameters<typeof saveConfig>[1]);

  const tiers = (config.intelligence as Record<string, unknown>).tiers as Record<string, { provider: string; model: string }>;

  const items: import("../../panels/Panel.js").PanelItem[] = VALID_TIERS.map((tier) => ({
    id: `tier-${tier}`,
    label: tier,
    meta: `${tiers[tier]?.provider ?? "—"} / ${tiers[tier]?.model ?? "—"}`,
    edit: {
      kind: "drill" as const,
      onEnter: () => {
        const dotPath = `intelligence.tiers.${tier}`;
        const items2 = buildItemsForValue(
          tiers[tier],
          dotPath,
          ctx,
          basePath,
          () => handleConfigTiers(ctx, []),
        );
        ctx.bridge.openPanel(`config:${dotPath}`, {
          title: `/config · intelligence · tiers · ${tier}`,
          items: items2,
          emptyText: "Empty.",
        });
      },
    },
  }));

  return {
    kind: "panel",
    payload: { title: "/config · tiers", items, emptyText: "No tiers configured." },
  };
};

/** /config set-tier <low|mid|high> <provider> <model> */
export const handleConfigSetTier: CommandHandler = async (ctx, args) => {
  const [tierArg, provider, model] = args;

  if (!tierArg || !VALID_TIERS.includes(tierArg as Tier)) {
    return { kind: "error", text: `Usage: /config set-tier <low|mid|high> <provider> <model>` };
  }
  if (!provider || !model) {
    return { kind: "error", text: `Usage: /config set-tier ${tierArg} <provider> <model>` };
  }

  const tier     = tierArg as Tier;
  const gateway  = ctx.getOwlGateway();
  const config   = gateway.getConfig() as unknown as Record<string, unknown>;
  const basePath = gateway.getWorkspacePath();

  ensureIntelligenceBlock(config);

  const intelligence = config.intelligence as Record<string, unknown>;
  const tiers = intelligence.tiers as Record<string, unknown>;
  tiers[tier] = { provider, model };

  try {
    await saveConfig(basePath, config as unknown as Parameters<typeof saveConfig>[1]);
    ctx.bridge.emit({
      kind: "notice",
      source: "command",
      text: `Tier ${tier} → ${provider} / ${model}`,
      severity: "info",
    });
    return { kind: "action" };
  } catch (e) {
    return { kind: "error", text: `Failed to save: ${(e as Error).message}` };
  }
};

// ─── Public handler (returned as CommandResult) ───────────────────────────────

export const handleConfigList: CommandHandler = async (ctx) => {
  const config = ctx.getOwlGateway().getConfig();
  const basePath = ctx.getOwlGateway().getWorkspacePath();
  const items = buildItemsForValue(config, "", ctx, basePath, () => reopenRoot(ctx, basePath));
  return {
    kind: "panel",
    payload: { title: "/config", items, emptyText: "No config loaded." },
  };
};

// ─── Drill-down helpers ───────────────────────────────────────────────────────

function reopenRoot(ctx: CommandContext, basePath: string): void {
  const config = ctx.getOwlGateway().getConfig();
  const items = buildItemsForValue(config, "", ctx, basePath, () => reopenRoot(ctx, basePath));
  ctx.bridge.openPanel("config:root", {
    title: "/config",
    items,
    emptyText: "No config loaded.",
  });
}

function reopenSection(ctx: CommandContext, basePath: string, dotPath: string): void {
  const config = ctx.getOwlGateway().getConfig();
  const node = getAtPath(config, dotPath);
  const breadcrumb = dotPath.split(".").join(" · ");
  const items = buildItemsForValue(
    node,
    dotPath,
    ctx,
    basePath,
    () => reopenSection(ctx, basePath, dotPath),
  );
  ctx.bridge.openPanel(`config:${dotPath}`, {
    title: `/config · ${breadcrumb}`,
    items,
    emptyText: "Empty.",
  });
}

// ─── Item builder ─────────────────────────────────────────────────────────────

/**
 * Build PanelItem[] for any config node (object, array, or scalar).
 * `parentPath` is the dot-path of the current node ("" = root).
 * `onRefresh` is called after a successful edit to re-open the current panel.
 */
function buildItemsForValue(
  node: unknown,
  parentPath: string,
  ctx: CommandContext,
  basePath: string,
  onRefresh: () => void,
): PanelItem[] {
  if (node === null || node === undefined) return [];

  if (Array.isArray(node)) {
    return node.map((el, i) => {
      const dotPath = parentPath ? `${parentPath}.${i}` : String(i);
      return buildScalarOrDrillItem(el, dotPath, String(i), ctx, basePath, onRefresh);
    });
  }

  if (typeof node === "object") {
    return Object.entries(node as Record<string, unknown>).map(([key, val]) => {
      const dotPath = parentPath ? `${parentPath}.${key}` : key;
      return buildScalarOrDrillItem(val, dotPath, key, ctx, basePath, onRefresh);
    });
  }

  // Scalar root (unlikely but handle gracefully)
  const dotPath = parentPath;
  return [buildScalarOrDrillItem(node, dotPath, dotPath, ctx, basePath, onRefresh)];
}

function buildScalarOrDrillItem(
  value: unknown,
  dotPath: string,
  label: string,
  ctx: CommandContext,
  basePath: string,
  onRefresh: () => void,
): PanelItem {
  const type = inferType(value);
  const isSecret = isSecretPath(dotPath);

  const metaDisplay = (): string => {
    if (isSecret && typeof value === "string") return maskSecret(value);
    return displayValue(value);
  };

  if (type === "object" || type === "array") {
    return {
      id: dotPath,
      label,
      meta: type === "array" ? `[${(value as unknown[]).length}]` : "{…}",
      edit: {
        kind: "drill",
        onEnter: () => reopenSection(ctx, basePath, dotPath),
      },
    };
  }

  if (type === "boolean") {
    return {
      id: dotPath,
      label,
      meta: String(value),
      edit: {
        kind: "boolean",
        currentValue: Boolean(value),
        onToggle: () => applyEdit(ctx, basePath, dotPath, !value, onRefresh),
      },
    };
  }

  if (type === "number") {
    return {
      id: dotPath,
      label,
      meta: String(value),
      edit: {
        kind: "number",
        currentValue: Number(value),
        onSubmit: (n: number) => applyEdit(ctx, basePath, dotPath, n, onRefresh),
      },
    };
  }

  // string or null
  if (isSecret) {
    return {
      id: dotPath,
      label,
      meta: typeof value === "string" ? maskSecret(value) : "<unset>",
      edit: {
        kind: "string",
        currentValue: "",   // never prefill secrets
        mask: true,
        onSubmit: (raw: string) => applyEdit(ctx, basePath, dotPath, raw, onRefresh),
      },
    };
  }

  return {
    id: dotPath,
    label,
    meta: metaDisplay(),
    edit: {
      kind: "string",
      currentValue: typeof value === "string" ? value : "",
      onSubmit: (raw: string) => {
        const parsed = parseScalarInput(raw, type as ConfigValueType);
        if (!parsed.ok) {
          ctx.bridge.emit({
            kind: "notice",
            source: "command",
            text: `config: ${parsed.error}`,
            severity: "error",
          });
          return;
        }
        return applyEdit(ctx, basePath, dotPath, parsed.value, onRefresh);
      },
    },
  };
}

// ─── Mutation + persistence ───────────────────────────────────────────────────

async function applyEdit(
  ctx: CommandContext,
  basePath: string,
  dotPath: string,
  newValue: unknown,
  onRefresh: () => void,
): Promise<void> {
  const live = ctx.getOwlGateway().getConfig();
  const before = getAtPath(live, dotPath);

  // Apply mutation in-place
  setAtPath(live, dotPath, newValue);

  try {
    // saveConfig validates internally and throws on invalid
    await saveConfig(basePath, live);
    const display = maskIfSecret(dotPath, newValue);
    ctx.bridge.emit({
      kind: "notice",
      source: "command",
      text: `config: ${dotPath} → ${display}`,
      severity: "info",
    });
    // Refresh panel so meta values update
    onRefresh();
  } catch (e) {
    // Rollback in-memory mutation
    setAtPath(live, dotPath, before);
    ctx.bridge.emit({
      kind: "notice",
      source: "command",
      text: `config error: ${(e as Error).message}`,
      severity: "error",
    });
  }
}
