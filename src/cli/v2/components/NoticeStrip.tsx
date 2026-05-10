/** Dim one-line pill for instincts/perches/skills that fired. Phase 1. */

import { Text } from "ink";
import type { Notice } from "../state/slices/heartbeat.js";
export function NoticeStrip({ notice }: { notice: Notice }) {
  return <Text dimColor>∷ [{notice.source}] {notice.text}</Text>;
}
