import { useState, useEffect } from "react";
import { Box, Text } from "ink";
import {
  STACKOWL_SPINNER,
  THINKING_SPIN_INTERVAL_MS,
  SPINNER_AMBER,
  FADE_COLORS,
  THINKING_MESSAGES,
} from "./spinner.js";

/**
 * Animated "Working on it..." indicator shown while the owl is thinking.
 * Spinner icon blinks on the left; text cycles through 30 languages with
 * a yellow→red→yellow colour fade.
 */
export function ThinkingIndicator() {
  const [spinFrame, setSpinFrame] = useState(0);
  const [langIdx] = useState(() => Math.floor(Math.random() * THINKING_MESSAGES.length));

  useEffect(() => {
    const t = setInterval(
      () => setSpinFrame((f) => (f + 1) % STACKOWL_SPINNER.length),
      THINKING_SPIN_INTERVAL_MS,
    );
    return () => clearInterval(t);
  }, []);

  const color = FADE_COLORS[spinFrame % FADE_COLORS.length];

  return (
    <Box>
      <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[spinFrame]} </Text>
      <Text bold color={color}>{THINKING_MESSAGES[langIdx]}</Text>
    </Box>
  );
}
