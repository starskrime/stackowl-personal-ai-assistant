/** Signed message author chip: emoji + bold name + dim role. */

import { Box, Text } from "ink";
import { SPINNER_AMBER } from "./spinner.js";

export interface OwlAvatarProps {
  emoji: string;
  name: string;
  role?: string;
  /** Override name color. Defaults to amber brand accent. */
  color?: string;
}

export function OwlAvatar({ emoji, name, role, color = SPINNER_AMBER }: OwlAvatarProps) {
  return (
    <Box>
      <Text>{emoji} </Text>
      <Text bold color={color}>{name}</Text>
      {role ? <Text dimColor>  {role}</Text> : null}
    </Box>
  );
}
