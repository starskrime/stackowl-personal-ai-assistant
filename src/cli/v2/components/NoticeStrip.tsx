/** Dim one-line pill for instincts/perches/skills that fired. Phase 1. */

import { Text } from "ink";
import type { Notice } from "../state/slices/heartbeat.js";

export interface NoticeStripProps {
  notice: Notice;
}

export function NoticeStrip({ notice }: NoticeStripProps) {
  if (notice.severity === "error") {
    return (
      <Text color="red">
        ✕ [{notice.source}] {notice.text}
      </Text>
    );
  }
  return (
    <Text dimColor>
      ∷ [{notice.source}] {notice.text}
    </Text>
  );
}
