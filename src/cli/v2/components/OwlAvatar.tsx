/** Signed message author chip: emoji + name + role badge. Phase 1. */

import { Text } from "ink";

export interface OwlAvatarProps {
  emoji: string;
  name: string;
  role?: string;
  color?: string;
}

export function OwlAvatar({ emoji, name, role, color }: OwlAvatarProps) {
  return (
    <Text color={color ?? "cyan"}>
      {emoji} <Text bold>{name}</Text>
      {role ? <Text dimColor> · {role}</Text> : null}
    </Text>
  );
}
