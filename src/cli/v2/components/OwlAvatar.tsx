/** Signed message author chip: emoji + bold name + dim role. */

import { Box, Text } from "ink";

export interface OwlAvatarProps {
  emoji: string;
  name: string;
  role?: string;
  color?: string;
}

export function OwlAvatar({ emoji, name, role, color = "cyan" }: OwlAvatarProps) {
  return (
    <Box>
      <Text>{emoji} </Text>
      <Text bold color={color}>{name}</Text>
      {role ? <Text dimColor>  {role}</Text> : null}
    </Box>
  );
}
