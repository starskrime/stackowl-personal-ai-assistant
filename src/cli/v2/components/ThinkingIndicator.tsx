import { useState, useEffect } from "react";
import { Box, Text } from "ink";
import {
  STACKOWL_SPINNER,
  SPINNER_INTERVAL_MS,
  SPINNER_AMBER,
  FADE_COLORS,
  LANG_INTERVAL_MS,
  THINKING_MESSAGES,
} from "./spinner.js";

/**
 * Animated "Working on it..." indicator shown while the owl is thinking.
 * Spinner icon blinks on the left; text cycles through 30 languages with
 * a yellow→red→yellow colour fade.
 */
export function ThinkingIndicator() {
  const [spinFrame, setSpinFrame] = useState(0);
  const [langIdx,   setLangIdx]   = useState(0);

  useEffect(() => {
    const t = setInterval(
      () => setSpinFrame((f) => (f + 1) % STACKOWL_SPINNER.length),
      SPINNER_INTERVAL_MS,
    );
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const t = setInterval(
      () => setLangIdx((i) => (i + 1) % THINKING_MESSAGES.length),
      LANG_INTERVAL_MS,
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
