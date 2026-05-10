/** Signed message author chip: emoji + name + role badge. Phase 1. */

import { Text } from "ink";
export function OwlAvatar({ emoji, name, role }: { emoji: string; name: string; role?: string }) {
  return <Text>{emoji} <Text bold>{name}</Text>{role ? <Text dimColor> · {role}</Text> : null}</Text>;
}
