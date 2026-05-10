/**
 * ParliamentScreen — full-width multi-owl debate theater.
 *
 * Layout:
 *   ⚖ Parliament  Round 1 of 3  Initial Positions
 *   ─────────────────────────────────────────────────
 *   ┌─ 🦉 Hoots ──┐  ┌─ 🦅 Sage ──┐  ┌─ 🐦 Wren ──┐
 *   │ position    │  │ position   │  │ position   │
 *   └─────────────┘  └────────────┘  └────────────┘
 *   ╔═ ⚖ Parliament Verdict  (by Sage) ════════════╗
 *   ║  synthesis text here                         ║
 *   ╚══════════════════════════════════════════════╝
 *   ─────────────────────────────────────────────────
 *   Ctrl+P — return to chat  ·  Round 1 in progress...
 */

import { Box, Text, useStdout } from "ink";
import { useState, useEffect } from "react";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";
import type { ParliamentDebate } from "../state/slices/parliament.js";
import { STACKOWL_SPINNER, SPINNER_AMBER, SPINNER_INTERVAL_MS } from "../components/spinner.js";
import { useTheme } from "../providers/ThemeProvider.js";

function roundLabel(round: number): string {
  switch (round) {
    case 1: return "Initial Positions";
    case 2: return "Cross-Examination";
    case 3: return "Synthesis";
    default: return `Round ${round}`;
  }
}

// ─── OwlColumn ────────────────────────────────────────────────────────────────

interface OwlColumnProps {
  owlName:   string;
  owlEmoji:  string;
  position?: string;
  challenge?: string;
  round:     number;
  colWidth:  number;
  spinning:  boolean;
  spinFrame: number;
}

function OwlColumn({
  owlName, owlEmoji, position, challenge,
  colWidth, spinning, spinFrame,
}: OwlColumnProps) {
  const { colors } = useTheme();
  const text   = challenge ?? position ?? "";
  const ready  = !!(challenge ?? position);
  const status = challenge ? "cross-exam" : position ? "ready" : "thinking...";

  return (
    <Box
      flexDirection="column"
      width={colWidth}
      borderStyle="single"
      borderColor={ready ? colors.accent : colors.dim}
      paddingX={1}
    >
      <Box>
        <Text bold color={SPINNER_AMBER}>{owlEmoji} {owlName}</Text>
        <Text> </Text>
        {spinning && !ready ? (
          <>
            <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[spinFrame]} </Text>
            <Text dimColor>{status}</Text>
          </>
        ) : (
          <Text color={ready ? (challenge ? colors.warning : colors.success) : colors.dim} dimColor={!ready}>
            [{status}]
          </Text>
        )}
      </Box>
      <Box marginTop={1}>
        {ready
          ? <Text wrap="wrap">{text}</Text>
          : <Text dimColor>Preparing position...</Text>
        }
      </Box>
    </Box>
  );
}

// ─── SynthesisPanel ──────────────────────────────────────────────────────────

function SynthesisPanel({ debate }: { debate: ParliamentDebate }) {
  const { colors } = useTheme();
  if (!debate.synthesis) return null;
  return (
    <Box
      flexDirection="column"
      borderStyle="double"
      borderColor={colors.verdict}
      paddingX={2}
      paddingY={1}
      marginTop={1}
    >
      <Box>
        <Text bold color={colors.verdict}>⚖  Parliament Verdict</Text>
        {debate.synthOwlName && (
          <Text dimColor>   by {debate.synthOwlName}</Text>
        )}
      </Box>
      <Box marginTop={1}>
        <Text wrap="wrap">{debate.synthesis}</Text>
      </Box>
    </Box>
  );
}

// ─── ParliamentScreen ────────────────────────────────────────────────────────

export function ParliamentScreen() {
  const { colors } = useTheme();
  const debate = useUiStore((s) => s.activeDebate);
  const { stdout } = useStdout();
  const [cols, setCols] = useState(stdout?.columns ?? 80);
  const [spinFrame, setSpinFrame] = useState(0);

  useEffect(() => {
    const h = () => setCols(stdout?.columns ?? 80);
    stdout?.on("resize", h);
    return () => { stdout?.off("resize", h); };
  }, [stdout]);

  useEffect(() => {
    if (!debate?.active) return;
    const t = setInterval(() => setSpinFrame((f) => (f + 1) % STACKOWL_SPINNER.length), SPINNER_INTERVAL_MS);
    return () => clearInterval(t);
  }, [debate?.active]);

  useEffect(() => {
    if (debate && !debate.active && debate.synthesis) {
      const t = setTimeout(() => globalBridge.dismissParliamentView(), 3000);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [debate]);

  const divider = "─".repeat(Math.max(0, cols));

  if (!debate) {
    return (
      <Box flexDirection="column" padding={2}>
        <Text bold color={colors.accent}>⚖  Parliament</Text>
        <Box marginTop={1}>
          <Text dimColor>No active debate. Ask a complex question to convene Parliament.</Text>
        </Box>
        <Box marginTop={1}>
          <Text dimColor>Ctrl+P — return to chat</Text>
        </Box>
      </Box>
    );
  }

  const owls = debate.owls;
  const colWidth = Math.max(20, Math.floor((cols - 2) / Math.max(1, owls.length)));

  return (
    <Box flexDirection="column" width={cols}>
      {/* Header */}
      <Box paddingX={1} paddingY={0}>
        <Text bold color={colors.accent}>⚖  Parliament</Text>
        <Text dimColor>  Round {debate.round} of {debate.totalRounds}</Text>
        <Text dimColor>  ·  </Text>
        <Text color={colors.warning}>{roundLabel(debate.round)}</Text>
        {!debate.active && debate.synthesis && (
          <Text dimColor>  ·  returning to chat in 3s...</Text>
        )}
      </Box>

      <Text dimColor>{divider}</Text>

      {/* Owl columns */}
      <Box flexDirection="row" flexWrap="nowrap">
        {owls.map((owl) => (
          <OwlColumn
            key={owl.owlId}
            owlName={owl.owlName}
            owlEmoji={owl.owlEmoji || "🦉"}
            position={debate.positions[owl.owlId]}
            challenge={debate.challenges[owl.owlId]}
            round={debate.round}
            colWidth={colWidth}
            spinning={!!debate.active}
            spinFrame={spinFrame}
          />
        ))}
      </Box>

      {debate.synthesis && <SynthesisPanel debate={debate} />}

      <Text dimColor>{divider}</Text>
      <Box paddingX={1}>
        <Text dimColor>Ctrl+P — return to chat</Text>
        {debate.active && (
          <Text dimColor>  ·  {roundLabel(debate.round)} in progress...</Text>
        )}
      </Box>
    </Box>
  );
}
