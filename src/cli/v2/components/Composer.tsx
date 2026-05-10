/**
 * Composer — registry-driven input editor + completions popup.
 *
 * Idle layout (bordered box):
 *   ╭─────────────────────────────────────────────────╮
 *   │  ❯ your message here▋                           │
 *   │  /help · /owls · /sessions · /memory · /skills  │
 *   ╰─────────────────────────────────────────────────╯
 *
 * Generating layout:
 *   ╭─────────────────────────────────────────────────╮
 *   │  ✳ generating...                               │
 *   ╰─────────────────────────────────────────────────╯
 *
 * When panelFocus === "panel", input is dimmed and disabled.
 */

import { useState, useRef, useEffect } from "react";
import { Box, Text, useInput, useApp } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";
import { InputHistory } from "../input/history.js";
import { stripPasteMarkers, isPasteChunk } from "../input/paste.js";
import { globalBridge } from "../events/bridge.js";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { useCommandDispatcher } from "../providers/CommandDispatcherProvider.js";
import { STACKOWL_SPINNER, SPINNER_AMBER, SPINNER_INTERVAL_MS } from "./spinner.js";
import { getCompletions } from "../commands/completion.js";
import type { CompletionEntry } from "../commands/completion.js";
import type { CommandContext } from "../commands/registry.js";
import { uiStore } from "../state/store.js";

export interface ComposerProps {
  onSubmit: (text: string) => void;
  disabled: boolean;
}

export function Composer({ onSubmit, disabled }: ComposerProps) {
  const [value, setValue] = useState("");
  const [genFrame, setGenFrame] = useState(0);
  const [completions, setCompletions] = useState<CompletionEntry[]>([]);
  const [completionIdx, setCompletionIdx] = useState(0);
  const historyRef = useRef<InputHistory>(new InputHistory());
  const { exit } = useApp();
  const { colors } = useTheme();
  const dispatcher = useCommandDispatcher();

  const mode          = useUiStore((s) => s.mode);
  const generating    = useUiStore((s) => s.generating);
  const panelFocus    = useUiStore((s) => s.panelFocus);
  const activeOwlName = useUiStore((s) => s.activeOwlName);
  const activeOwlEmoji = useUiStore((s) => s.activeOwlEmoji);

  // CommandContext shell for completions (bridge + store only)
  // Stable ref — globalBridge and uiStore are module-level singletons so this never changes
  const completionCtxRef = useRef<CommandContext>({
    bridge: globalBridge,
    getStore: () => uiStore.getState(),
    getMemoryRepo: () => { throw new Error("not available in Composer"); },
    getMcpManager: () => { throw new Error("not available in Composer"); },
    getOwlGateway: () => { throw new Error("not available in Composer"); },
  });

  useEffect(() => {
    if (!generating) return;
    const t = setInterval(() => setGenFrame((f) => (f + 1) % STACKOWL_SPINNER.length), SPINNER_INTERVAL_MS);
    return () => clearInterval(t);
  }, [generating]);

  // Recompute completions whenever value changes
  useEffect(() => {
    if (!value.startsWith("/")) { setCompletions([]); setCompletionIdx(0); return; }
    let cancelled = false;
    getCompletions(value, completionCtxRef.current).then((results) => {
      if (!cancelled) { setCompletions(results); setCompletionIdx(0); }
    });
    return () => { cancelled = true; };
  }, [value]);

  const showPopup = completions.length > 0 && value !== (completions[0]?.value ?? "");

  useInput(
    (input, key) => {
      if (key.ctrl && input === "c") { exit(); return; }
      if (key.ctrl && input === "d" && value === "") { exit(); return; }
      if (key.ctrl && input === "l") {
        dispatcher.dispatch("/clear").then((result) => {
          if (result.kind === "error") {
            globalBridge.emit({ kind: "notice", source: "command", text: result.text, severity: "error" });
          }
        }).catch((e) => process.stderr.write(`[Composer] /clear dispatch error: ${e}\n`));
        return;
      }

      if (key.ctrl && input === "p") {
        if (mode === "parliament") globalBridge.dismissParliamentView();
        else                       globalBridge.requestParliamentView();
        return;
      }

      // Shift+Tab: cycle through available owl personas
      if (key.shift && key.tab) {
        const { availableOwls, activeOwlName } = uiStore.getState();
        if (availableOwls.length > 1) {
          const cur = availableOwls.findIndex((o) => o.name.toLowerCase() === activeOwlName.toLowerCase());
          const next = availableOwls[(cur + 1) % availableOwls.length]!;
          globalBridge.changeOwl(next.name, next.emoji);
        }
        return;
      }

      // Arrow navigation inside completions popup
      if (showPopup) {
        if (key.upArrow)   { setCompletionIdx((i) => (i - 1 + completions.length) % completions.length); return; }
        if (key.downArrow) { setCompletionIdx((i) => (i + 1) % completions.length); return; }
        if (key.escape)    { setValue(""); return; }
        if (key.tab) {
          const entry = completions[completionIdx];
          if (entry) {
            if (entry.kind === "command") setValue(entry.value);
            else if (entry.kind === "subcommand") {
              // find the command prefix (everything up to and including the first word)
              const cmdPart = value.trimEnd().split(/\s+/)[0] ?? "";
              setValue(cmdPart + " " + entry.value + " ");
            }
            else setValue(value.replace(/\S*$/, entry.value) + " ");
          }
          return;
        }
      }

      if (key.return && !key.shift) {
        const trimmed = value.trim();

        // Popup open + Enter → accept selected completion
        if (showPopup && completions.length > 0) {
          const entry = completions[completionIdx];
          if (entry) {
            if (entry.kind === "command") setValue(entry.value + " ");
            else if (entry.kind === "subcommand") setValue(value.replace(/\S+$/, "").trimEnd() + " " + entry.value + " ");
          }
          return;
        }

        // Slash command → dispatch
        if (trimmed.startsWith("/")) {
          dispatcher.dispatch(trimmed).then((result) => {
            if (result.kind === "error") {
              globalBridge.emit({ kind: "notice", source: "command", text: result.text, severity: "error" });
            }
            if (trimmed === "/quit" || trimmed === "/exit" || trimmed === "/bye") exit();
          }).catch((e) => process.stderr.write(`[Composer] dispatch error: ${e}\n`));
          historyRef.current.push(trimmed);
          setValue("");
          return;
        }

        // AI message
        if (trimmed) { historyRef.current.push(trimmed); onSubmit(trimmed); }
        setValue("");
        return;
      }

      if (key.backspace || key.delete) { setValue((v) => v.slice(0, -1)); return; }

      // Up/down arrow = history navigation when popup NOT open
      if (!showPopup) {
        if (key.upArrow)   { const p = historyRef.current.prev(value); if (p !== null) setValue(p); return; }
        if (key.downArrow) { const n = historyRef.current.next(); setValue(n !== null ? n : ""); return; }
      }

      if (isPasteChunk(input)) { setValue((v) => v + stripPasteMarkers(input)); return; }
      if (!key.ctrl && !key.meta && input.length === 1) { setValue((v) => v + input); return; }
    },
    { isActive: !disabled },
  );

  return (
    <Box flexDirection="column">
      {/* Completions popup */}
      {showPopup && (
        <Box flexDirection="column" borderStyle="single" borderTop borderBottom borderLeft={false} borderRight={false} borderColor={colors.accent}>
          {completions.map((entry, i) => (
            <Box key={entry.value}>
              <Text color={i === completionIdx ? colors.accent : undefined} bold={i === completionIdx}>
                {i === completionIdx ? "❯ " : "  "}
                {entry.value}
              </Text>
              {entry.description && (
                <Text dimColor>{"  " + entry.description.slice(0, 45)}</Text>
              )}
            </Box>
          ))}
        </Box>
      )}

      {/* Main input box — border owned by ChatScreen wrapper */}
      <Box flexDirection="column">
        {generating ? (
          <Box paddingLeft={1}>
            <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[genFrame]} </Text>
            <Text dimColor>generating...</Text>
          </Box>
        ) : (
          <>
            <Box paddingLeft={1}>
              <Text dimColor>{activeOwlEmoji} {activeOwlName} </Text>
              <Text bold color={panelFocus === "panel" ? colors.dim : colors.user}>❯ </Text>
              <Text color={panelFocus === "panel" ? colors.dim : undefined}>{value}</Text>
              {panelFocus !== "panel" && <Text color={colors.accent}>▋</Text>}
            </Box>
          </>
        )}
      </Box>
    </Box>
  );
}
