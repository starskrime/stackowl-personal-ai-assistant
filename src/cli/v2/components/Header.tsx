/** Per-turn owl identity banner. Phase 1. */

import { Box } from "ink";
import { OwlAvatar } from "./OwlAvatar.js";

export interface HeaderProps {
  emoji: string;
  name: string;
  role?: string;
}

export function Header({ emoji, name, role }: HeaderProps) {
  return (
    <Box>
      <OwlAvatar emoji={emoji} name={name} role={role} />
    </Box>
  );
}
