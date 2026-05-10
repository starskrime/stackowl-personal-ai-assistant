/** Per-turn owl identity banner. Phase 1. */

import { Box } from "ink";
import { OwlAvatar } from "./OwlAvatar.js";
export function Header({ emoji, name, role }: { emoji: string; name: string; role?: string }) {
  return <Box><OwlAvatar emoji={emoji} name={name} role={role} /></Box>;
}
