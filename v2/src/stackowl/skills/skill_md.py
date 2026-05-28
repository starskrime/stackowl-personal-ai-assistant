"""SKILL.md parser — frontmatter (YAML) + markdown body.

Matches the Anthropic / Hermes-Agent / Claude-Code-Skills convention so
StackOwl skills are portable across the wider ecosystem. Format:

    ---
    name: my-skill
    description: When to use this skill
    version: 0.1.0
    ---

    # Skill body in markdown
    Steps, references, examples...

Only the frontmatter is structured. The body is opaque markdown handed to
the LLM as advice in the system prompt (via classify.py's fifth gather).
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

_FRONTMATTER_DELIMITER = "---"


class SkillMarkdownError(Exception):
    """Raised when SKILL.md is malformed (missing frontmatter, invalid YAML)."""


@dataclass(frozen=True)
class SkillMarkdown:
    """Parsed SKILL.md: structured frontmatter dict + raw markdown body."""

    frontmatter: dict[str, object]
    body: str


def parse_skill_md(text: str) -> SkillMarkdown:
    """Parse a SKILL.md string into (frontmatter, body).

    Raises :class:`SkillMarkdownError` if frontmatter is missing/malformed.
    Body is everything after the closing ``---`` delimiter, stripped.
    """
    stripped = text.lstrip()
    if not stripped.startswith(_FRONTMATTER_DELIMITER):
        raise SkillMarkdownError(
            "SKILL.md must start with a YAML frontmatter block delimited by '---'",
        )
    # Split off the leading delimiter line, then find the closing one.
    after_first = stripped[len(_FRONTMATTER_DELIMITER):].lstrip("\n")
    closing_idx = after_first.find(f"\n{_FRONTMATTER_DELIMITER}")
    if closing_idx == -1:
        raise SkillMarkdownError(
            "SKILL.md frontmatter is missing a closing '---' delimiter",
        )
    fm_text = after_first[:closing_idx]
    body = after_first[closing_idx + len(_FRONTMATTER_DELIMITER) + 1:].lstrip("\n")
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise SkillMarkdownError(f"SKILL.md frontmatter is invalid YAML: {exc}") from exc
    if not isinstance(fm, dict):
        raise SkillMarkdownError(
            "SKILL.md frontmatter must be a YAML mapping (key: value pairs)",
        )
    return SkillMarkdown(frontmatter=fm, body=body.strip())
