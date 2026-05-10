import type { CommandHandler } from "../registry.js";
import { REGISTRY } from "../registry.js";

// ─── /capabilities ────────────────────────────────────────────────────────────

export const handleCapabilities: CommandHandler = async (ctx, _args) => {
  const ledger = ctx.getOwlGateway().getCapabilityLedger();
  if (!ledger) {
    return { kind: "error", text: "Capability ledger not available." };
  }
  const tools = ledger.listAll();
  if (tools.length === 0) {
    return {
      kind: "panel",
      payload: { title: "/capabilities", items: [], emptyText: "No synthesized tools yet." },
    };
  }
  const items = tools.map((t, i) => ({
    id: `cap-${i}`,
    label: t.toolName,
    meta: `${t.status}  ${t.timesUsed}x`,
  }));
  return { kind: "panel", payload: { title: "/capabilities", items } };
};

// ─── /learning ────────────────────────────────────────────────────────────────

export const handleLearning: CommandHandler = async (ctx, _args) => {
  const orchestrator = ctx.getOwlGateway().getLearningOrchestrator();
  if (!orchestrator) {
    return { kind: "error", text: "Learning engine not available." };
  }
  const report = await orchestrator.getFullReport();
  const items = report
    .split("\n")
    .filter((l) => l.trim())
    .map((line, i) => ({ id: `ln-${i}`, label: line }));
  return { kind: "panel", payload: { title: "/learning", items } };
};

// ─── /owl status ──────────────────────────────────────────────────────────────

export const handleOwlStatus: CommandHandler = async (ctx, _args) => {
  const gateway = ctx.getOwlGateway();
  const db = gateway.getDb();
  const owl = gateway.getOwl();
  if (!db) {
    return { kind: "error", text: "Database not available." };
  }
  const { OwlStateReporter } = await import("../../../../intelligence/owl-state-reporter.js");
  const reporter = new OwlStateReporter(db);
  const dna = owl.dna.evolvedTraits as Record<string, unknown>;
  const report = await reporter.report("local", owl.persona.name, dna);
  const items = report
    .split("\n")
    .filter((l) => l.trim())
    .map((line, i) => ({ id: `owl-${i}`, label: line }));
  return { kind: "panel", payload: { title: "/owl status", items } };
};

// ─── /help ────────────────────────────────────────────────────────────────────

export const handleHelp: CommandHandler = async (_ctx, _args) => {
  const items = REGISTRY.map((spec) => ({
    id: spec.name,
    label: spec.name + (spec.aliases?.length ? ` (${spec.aliases.join(", ")})` : ""),
    meta: spec.description,
  }));
  return {
    kind: "panel",
    payload: { title: "/help", items, emptyText: "No commands registered." },
  };
};
