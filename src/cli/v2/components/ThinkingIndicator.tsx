import { useState, useEffect } from "react";
import { Box, Text } from "ink";
import {
  STACKOWL_SPINNER,
  THINKING_SPIN_INTERVAL_MS,
  SPINNER_AMBER,
  FADE_COLORS,
  THINKING_MESSAGES,
} from "./spinner.js";
import { useUiStore } from "../providers/UiStoreProvider.js";

/**
 * Animated "Working on it..." indicator shown while the owl is thinking.
 * Spinner icon blinks on the left; text is sourced from the ProgressNotifier
 * (via thinkingPhrase in the store) or falls back to a random language.
 */
export function ThinkingIndicator() {
  const [spinFrame, setSpinFrame] = useState(0);
  const [fallbackIdx] = useState(() => Math.floor(Math.random() * THINKING_MESSAGES.length));
  const thinkingPhrase = useUiStore((s) => s.thinkingPhrase);

  useEffect(() => {
    const t = setInterval(
      () => setSpinFrame((f) => (f + 1) % STACKOWL_SPINNER.length),
      THINKING_SPIN_INTERVAL_MS,
    );
    return () => clearInterval(t);
  }, []);

  const color = FADE_COLORS[spinFrame % FADE_COLORS.length];
  const displayPhrase = thinkingPhrase ?? THINKING_MESSAGES[fallbackIdx]!;

  return (
    <Box>
      <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[spinFrame]} </Text>
      <Text bold color={color}>{displayPhrase}</Text>
    </Box>
  );
}
