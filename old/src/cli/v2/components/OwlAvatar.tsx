/** Signed message author chip: emoji + bold name + dim role. */

import { Box, Text } from "ink";
import { useTheme } from "../providers/ThemeProvider.js";

export interface OwlAvatarProps {
  emoji: string;
  name: string;
  role?: string;
  /** Override name color. Defaults to amber brand accent. */
  color?: string;
}

export function OwlAvatar({ emoji, name, role, color }: OwlAvatarProps) {
  const { colors } = useTheme();
  const nameColor = color ?? colors.brand;
  return (
    <Box>
      <Text>{emoji} </Text>
      <Text bold color={nameColor}>{name}</Text>
      {role ? <Text dimColor>  {role}</Text> : null}
    </Box>
  );
}
