"""BriefRenderer — converts a :class:`MorningBrief` to a plain-text frame.

Output format (UTF-8):

    ────────────────────────────────────────
    DATE_AND_PRIORITIES
      <item 1>
      <item 2>
    ────────────────────────────────────────
    MEMORY_HIGHLIGHTS
      ...

Section keys are uppercased and used as headers — there are no English
literals embedded in the rendered output, so swapping locales is a
matter of overriding ``section.title`` at assembly time, not editing
this file. Sections with ``omitted=True`` are silently skipped.
"""

from __future__ import annotations

from stackowl.brief.models import MorningBrief
from stackowl.infra.observability import log

_SEPARATOR_WIDTH = 40


class BriefRenderer:
    """Renders a :class:`MorningBrief` to a single string."""

    SEPARATOR: str = "─" * _SEPARATOR_WIDTH  # U+2500 BOX DRAWINGS LIGHT HORIZONTAL

    def render(self, brief: MorningBrief) -> str:
        # 1. ENTRY
        log.scheduler.debug(
            "[brief] renderer.render: entry",
            extra={
                "_fields": {
                    "section_count": len(brief.sections),
                    "channels": brief.delivery_channels,
                }
            },
        )

        lines: list[str] = []
        rendered_count = 0
        for section in brief.sections:
            # 2. DECISION — skip omitted sections without emitting the separator
            if section.omitted:
                log.scheduler.debug(
                    "[brief] renderer.render: skipping omitted section",
                    extra={"_fields": {"key": section.key}},
                )
                continue
            lines.append(self.SEPARATOR)
            # Header is section.key uppercased — no hardcoded English literal here.
            lines.append(section.key.upper())
            lines.extend(f"  {item}" for item in section.items)
            rendered_count += 1

        result = "\n".join(lines)
        # 4. EXIT
        log.scheduler.debug(
            "[brief] renderer.render: exit",
            extra={
                "_fields": {
                    "rendered_sections": rendered_count,
                    "output_len": len(result),
                }
            },
        )
        return result
