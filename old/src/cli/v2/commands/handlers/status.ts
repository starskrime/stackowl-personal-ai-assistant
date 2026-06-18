import type { CommandHandler } from "../registry.js";

export const handleStatus: CommandHandler = async (ctx, _args) => {
  const store = ctx.getStore();
  const gateway = ctx.getOwlGateway();
  const config = gateway.getConfig();

  const lines = [
    `Owl:      ${store.activeOwlEmoji} ${store.activeOwlName}`,
    `Model:    ${store.activeModel || config.defaultModel || "unknown"}`,
    `Provider: ${store.activeProvider || "default"}`,
    `Tokens:   ${store.totalTokens.toLocaleString()} (session)`,
    `Cost:     $${store.totalCostUsd.toFixed(4)} (session)`,
    `Context:  ${store.contextWindowPct}% used`,
  ];

  const items = lines.map((line, i) => ({
    id: `status-${i}`,
    label: line,
  }));

  return {
    kind: "panel",
    payload: { title: "/status", items },
  };
};
