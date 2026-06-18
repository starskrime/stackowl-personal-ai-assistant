/**
 * StackOwl — Interactive Test Session
 *
 * Tests the assistant end-to-end through the real gateway HTTP API.
 * Starts `npx tsx src/index.ts web` if not already running, fires
 * POST /api/chat for each test case, then prints a result table.
 *
 * Run: npx tsx test-session.ts
 */

import { spawn, type ChildProcess } from "node:child_process";

const GATEWAY_URL = process.env.GATEWAY_URL ?? "http://localhost:3000";
const GATEWAY_HEALTH = `${GATEWAY_URL}/health`;
const GATEWAY_CHAT = `${GATEWAY_URL}/api/chat`;
const SESSION_ID = `test_session_${Date.now()}`;
const DIVIDER = "─".repeat(60);

const TESTS = [
  // ── Basic conversation ────────────────────────────────────────
  { label: "greeting",        msg: "hi" },
  { label: "who are you",     msg: "who are you and what can you do?" },
  { label: "simple math",     msg: "what is 17 × 13?" },

  // ── Knowledge ─────────────────────────────────────────────────
  { label: "bloom filter",    msg: "explain what a Bloom filter is in 3 sentences" },
  { label: "code gen",        msg: "write a Python function that checks if a string is a palindrome" },

  // ── Tool use (shell) ──────────────────────────────────────────
  { label: "today's date",    msg: "what is today's date and time?" },
  { label: "list files",      msg: "list the TypeScript files in src/engine/" },

  // ── Memory ────────────────────────────────────────────────────
  { label: "remember pref",   msg: "remember that I prefer dark mode and concise answers" },
  { label: "recall pref",     msg: "what do you remember about my preferences?" },

  // ── Multi-step reasoning ──────────────────────────────────────
  { label: "tech decision",   msg: "I'm building a real-time collaborative doc editor with 3 engineers. What database should I use and why? Be concise." },
  { label: "code review",     msg: "what's wrong with this JS: function add(a,b){ return a+b }; add('1',2)" },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function isHealthy(): Promise<boolean> {
  try {
    const res = await fetch(GATEWAY_HEALTH, { signal: AbortSignal.timeout(2000) });
    return res.ok;
  } catch {
    return false;
  }
}

async function waitForGateway(timeoutMs = 30_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  process.stdout.write("  Waiting for gateway");
  while (Date.now() < deadline) {
    if (await isHealthy()) { process.stdout.write(" ✓\n"); return true; }
    process.stdout.write(".");
    await new Promise(r => setTimeout(r, 500));
  }
  process.stdout.write(" ✗ timed out\n");
  return false;
}

async function chat(message: string): Promise<{ content: string; durationMs: number }> {
  const start = Date.now();
  const res = await fetch(GATEWAY_CHAT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, sessionId: SESSION_ID }),
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${body.slice(0, 120)}`);
  }
  const data = await res.json() as { content?: string; text?: string; error?: string };
  if (data.error) throw new Error(data.error);
  const content = data.content ?? data.text ?? "";
  return { content, durationMs: Date.now() - start };
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function run() {
  console.log("\n🦉 StackOwl Interactive Test Session (via HTTP gateway)");
  console.log(DIVIDER + "\n");

  // Pre-flight: kill any stale Chrome processes holding browser profiles
  try {
    const { execSync } = await import("node:child_process");
    execSync("pkill -9 -f 'user-data-dir=.*browser-data' 2>/dev/null || true", { stdio: "ignore" });
    await new Promise(r => setTimeout(r, 600)); // let OS reap them
  } catch { /* ignore */ }

  let gatewayProcess: ChildProcess | null = null;
  let weStartedIt = false;

  // 1. Check if gateway is already up
  if (await isHealthy()) {
    console.log(`  Gateway already running at ${GATEWAY_URL}`);
  } else {
    // 2. Start it ourselves
    console.log(`  Starting gateway (npx tsx src/index.ts web) …`);
    gatewayProcess = spawn("npx", ["tsx", "src/index.ts", "web"], {
      stdio: ["ignore", "pipe", "pipe"],
      detached: true, // own process group so we can kill all children at once
    });
    weStartedIt = true;

    // Pipe gateway stderr/stdout to our stderr so we see startup errors
    gatewayProcess.stdout?.on("data", (d: Buffer) =>
      process.stderr.write(`  [gw] ${d.toString().trimEnd()}\n`));
    gatewayProcess.stderr?.on("data", (d: Buffer) =>
      process.stderr.write(`  [gw] ${d.toString().trimEnd()}\n`));

    gatewayProcess.on("error", (err) => {
      console.error(`  ❌ Failed to spawn gateway: ${err.message}`);
      process.exit(1);
    });

    const ready = await waitForGateway(90_000);
    if (!ready) {
      gatewayProcess.kill();
      process.exit(1);
    }
  }

  console.log(`  Endpoint : ${GATEWAY_CHAT}`);
  console.log(`  Session  : ${SESSION_ID}`);
  console.log(`  Tests    : ${TESTS.length}\n`);
  console.log(DIVIDER);

  // 3. Run tests
  const results: Array<{
    label: string;
    status: "ok" | "error" | "empty";
    durationMs: number;
    preview: string;
  }> = [];

  for (const test of TESTS) {
    console.log(`\n📨  ${test.label}`);
    console.log(`    User: ${test.msg}`);
    process.stdout.write(`    Owl:  `);

    try {
      const { content, durationMs } = await chat(test.msg);
      const text = content.trim();

      if (!text) {
        console.log("(empty)");
        results.push({ label: test.label, status: "empty", durationMs, preview: "" });
      } else {
        const preview = text.length > 220 ? text.slice(0, 220) + "…" : text;
        console.log(preview);
        console.log(`    ⏱  ${durationMs}ms`);
        results.push({ label: test.label, status: "ok", durationMs, preview: text.slice(0, 80) });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.log(`ERROR: ${msg.slice(0, 120)}`);
      results.push({ label: test.label, status: "error", durationMs: 0, preview: msg.slice(0, 80) });
    }
  }

  // 4. Summary
  console.log("\n" + DIVIDER);
  console.log("📊  RESULTS\n");

  const ok    = results.filter(r => r.status === "ok");
  const errors = results.filter(r => r.status === "error");
  const empty = results.filter(r => r.status === "empty");

  for (const r of results) {
    const icon = r.status === "ok" ? "✅" : r.status === "error" ? "❌" : "⬜";
    const time = `${r.durationMs}ms`.padStart(6);
    console.log(`  ${icon}  ${time}  ${r.label}`);
  }

  const avgMs = ok.length > 0
    ? Math.round(ok.reduce((s, r) => s + r.durationMs, 0) / ok.length)
    : 0;

  console.log(`\n  Passed: ${ok.length}/${results.length}  |  Errors: ${errors.length}  |  Empty: ${empty.length}`);
  console.log(`  Avg response time (ok): ${avgMs}ms`);

  if (errors.length > 0) {
    console.log("\n  Failures:");
    for (const e of errors) {
      console.log(`    [${e.label}]: ${e.preview}`);
    }
  }

  console.log(DIVIDER + "\n");

  // 5. Shutdown gateway + all Chrome children if we started it
  if (weStartedIt && gatewayProcess) {
    console.log("  Stopping gateway …");
    try {
      // Kill the whole process group so Chrome children die too
      process.kill(-gatewayProcess.pid!, "SIGKILL");
    } catch {
      gatewayProcess.kill("SIGKILL");
    }
    // Clean up any orphaned Chrome processes just in case
    try {
      const { execSync } = await import("node:child_process");
      execSync("pkill -9 -f 'user-data-dir=.*browser-data' 2>/dev/null || true", { stdio: "ignore" });
    } catch { /* ignore */ }
  }

  process.exit(errors.length > 0 ? 1 : 0);
}

run().catch(err => {
  console.error("Fatal:", err);
  process.exit(1);
});
