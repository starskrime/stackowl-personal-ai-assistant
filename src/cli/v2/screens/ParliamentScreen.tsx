/**
 * ParliamentScreen — alt-screen modal during multi-owl debates.
 *
 * Wired in Phase 2 (P2-A). Displays the active Parliament debate:
 *  - Header: round indicator
 *  - Body: one column per owl, showing their position text
 *  - Footer: round label / synthesis
 *  - Auto-returns to chat when synthesis completes
 */

import { Box, Text, useStdout } from "ink";
import { useState, useEffect } from "react";
import { useUiStore } from "../providers/UiStoreProvider.js";
import { globalBridge } from "../events/bridge.js";
import type { ParliamentDebate } from "../state/slices/parliament.js";

// ─── Round label helpers ──────────────────────────────────────────────────────

function roundLabel(round: number): string {
  switch (round) {
    case 1: return "Initial Positions";
    case 2: return "Cross-Examination";
    case 3: return "Synthesis";
    default: return `Round ${round}`;
  }
}

// ─── OwlColumn — one owl's position column ───────────────────────────────────

interface OwlColumnProps {
  owlName: string;
  owlEmoji: string;
  position?: string;
  challenge?: string;
  round: number;
  colWidth: number;
}

function OwlColumn({ owlName, owlEmoji, position, challenge, round, colWidth }: OwlColumnProps) {
  const hasContent = round >= 1 && (position ?? challenge);
  const text = challenge ?? position ?? "";
  const status = challenge ? "challenged" : position ? "ready" : "waiting...";
  const statusColor = challenge ? "yellow" : position ? "green" : "gray";

  return (
    <Box
      flexDirection="column"
      width={colWidth}
      borderStyle="single"
      borderColor="cyan"
      paddingX={1}
    >
      {/* Owl header */}
      <Box>
        <Text bold color="cyan">
          {owlEmoji} {owlName}
        </Text>
        <Text> </Text>
        <Text color={statusColor} dimColor={!hasContent}>
          [{status}]
        </Text>
      </Box>

      {/* Position / challenge text */}
      <Box marginTop={1} flexDirection="column">
        {hasContent ? (
          <Text wrap="wrap">{text}</Text>
        ) : (
          <Text dimColor>Preparing position...</Text>
        )}
      </Box>
    </Box>
  );
}

// ─── SynthesisPanel ──────────────────────────────────────────────────────────

interface SynthesisPanelProps {
  debate: ParliamentDebate;
}

function SynthesisPanel({ debate }: SynthesisPanelProps) {
  if (!debate.synthesis) return null;

  return (
    <Box
      flexDirection="column"
      borderStyle="double"
      borderColor="yellow"
      paddingX={2}
      paddingY={1}
      marginTop={1}
    >
      <Box>
        <Text bold color="yellow">
          {"⚖"} Parliament Verdict
        </Text>
        {debate.synthOwlName && (
          <Text dimColor>  (by {debate.synthOwlName})</Text>
        )}
      </Box>
      <Box marginTop={1}>
        <Text wrap="wrap">{debate.synthesis}</Text>
      </Box>
    </Box>
  );
}

// ─── ParliamentScreen ─────────────────────────────────────────────────────────

export function ParliamentScreen() {
  const debate = useUiStore((s) => s.activeDebate);
  const { stdout } = useStdout();
  const [cols, setCols] = useState(stdout?.columns ?? 80);

  useEffect(() => {
    const handler = () => setCols(stdout?.columns ?? 80);
    stdout?.on("resize", handler);
    return () => { stdout?.off("resize", handler); };
  }, [stdout]);

  // Auto-return to chat once synthesis completes and debate becomes inactive.
  // The bridge dismisses the parliament view after a short delay so the user
  // can read the synthesis before the screen switches back.
  useEffect(() => {
    if (debate && !debate.active && debate.synthesis) {
      const timer = setTimeout(() => {
        globalBridge.dismissParliamentView();
      }, 3000);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [debate]);

  if (!debate) {
    return (
      <Box flexDirection="column" padding={2}>
        <Text bold color="cyan">{"⚖️  Parliament"}</Text>
        <Text dimColor>No active debate. Start a complex question to convene Parliament.</Text>
        <Box marginTop={1}>
          <Text dimColor>Press Ctrl+P to return to chat.</Text>
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
        <Text bold color="cyan">
          {"⚖️  Parliament · Round "}{debate.round}{" of "}{debate.totalRounds}
        </Text>
        <Text> — </Text>
        <Text color="yellow">{roundLabel(debate.round)}</Text>
        {!debate.active && debate.synthesis && (
          <Text color="green" dimColor>  · Returning to chat in 3s...</Text>
        )}
      </Box>

      {/* Divider */}
      <Text>{"─".repeat(Math.max(0, cols))}</Text>

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
          />
        ))}
      </Box>

      {/* Synthesis panel (Round 3 complete) */}
      {debate.synthesis && <SynthesisPanel debate={debate} />}

      {/* Footer */}
      <Text>{"─".repeat(Math.max(0, cols))}</Text>
      <Box paddingX={1}>
        <Text dimColor>Ctrl+P — return to chat</Text>
        {debate.active && (
          <Text dimColor>  · {roundLabel(debate.round)} in progress...</Text>
        )}
      </Box>
    </Box>
  );
}
