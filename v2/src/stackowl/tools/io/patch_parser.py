"""V4A unified-diff patch parser.

Parses the V4A patch format (Update / Add / Delete / Move file hunks with
context disambiguation and fuzzy anchoring) used by several coding agents:

    *** Begin Patch
    *** Update File: path/to/file.py
    @@ optional context hint @@
     context line (space prefix)
    -removed line (minus prefix)
    +added line (plus prefix)
    *** Add File: path/to/new.py
    +new file content
    +line 2
    *** Delete File: path/to/old.py
    *** Move File: old/path.py -> new/path.py
    *** End Patch

This module is the PARSE half only — it turns patch text into a list of
:class:`PatchOperation`. The filesystem apply (path-guarding every target,
sorted multi-file locking, snapshot + atomic rollback) lives in
``apply_patch.py``; that side was translated to clean async StackOwl code
rather than ported, because the upstream apply path is coupled to a foreign
``file_ops`` interface.

Provenance / port-vs-build: PORT of the V4A grammar/parsing ALGORITHM — see
``_bmad-output/research/tool-port-analysis.md`` (E3 ``apply_patch`` row). No
mature self-hosted Python library *applies* V4A patches with context
disambiguation and fuzzy anchoring, so the 622-line battle-tested parser is
ported rather than re-derived (re-deriving the grammar would be a defect farm).

port-fidelity: verbatim. The only deviations from the source are:
  * type annotations added to satisfy strict mypy (parser LOGIC is unchanged);
  * the apply-phase functions (which depended on a foreign file-ops interface)
    are NOT ported here — they are reimplemented in ``apply_patch.py``;
  * vendor names neutralised per the no-vendor-names-in-code rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class OperationType(Enum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    MOVE = "move"


@dataclass
class HunkLine:
    """A single line in a patch hunk."""

    prefix: str  # ' ', '-', or '+'
    content: str


@dataclass
class Hunk:
    """A group of changes within a file."""

    context_hint: str | None = None
    lines: list[HunkLine] = field(default_factory=list)


@dataclass
class PatchOperation:
    """A single operation in a V4A patch."""

    operation: OperationType
    file_path: str
    new_path: str | None = None  # For move operations
    hunks: list[Hunk] = field(default_factory=list)
    content: str | None = None  # For add file operations


def parse_v4a_patch(patch_content: str) -> tuple[list[PatchOperation], str | None]:
    """Parse a V4A format patch.

    Args:
        patch_content: The patch text in V4A format

    Returns:
        Tuple of (operations, error_message)
        - If successful: (list_of_operations, None)
        - If failed: ([], error_description)
    """
    lines = patch_content.split("\n")
    operations: list[PatchOperation] = []

    # Find patch boundaries
    start_idx: int | None = None
    end_idx: int | None = None

    for i, line in enumerate(lines):
        if "*** Begin Patch" in line or "***Begin Patch" in line:
            start_idx = i
        elif "*** End Patch" in line or "***End Patch" in line:
            end_idx = i
            break

    if start_idx is None:
        # Try to parse without explicit begin marker
        start_idx = -1

    if end_idx is None:
        end_idx = len(lines)

    # Parse operations between boundaries
    i = start_idx + 1
    current_op: PatchOperation | None = None
    current_hunk: Hunk | None = None

    while i < end_idx:
        line = lines[i]

        # Check for file operation markers
        update_match = re.match(r"\*\*\*\s*Update\s+File:\s*(.+)", line)
        add_match = re.match(r"\*\*\*\s*Add\s+File:\s*(.+)", line)
        delete_match = re.match(r"\*\*\*\s*Delete\s+File:\s*(.+)", line)
        move_match = re.match(r"\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)", line)

        if update_match:
            # Save previous operation
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)

            current_op = PatchOperation(
                operation=OperationType.UPDATE,
                file_path=update_match.group(1).strip(),
            )
            current_hunk = None

        elif add_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)

            current_op = PatchOperation(
                operation=OperationType.ADD,
                file_path=add_match.group(1).strip(),
            )
            current_hunk = Hunk()

        elif delete_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)

            current_op = PatchOperation(
                operation=OperationType.DELETE,
                file_path=delete_match.group(1).strip(),
            )
            operations.append(current_op)
            current_op = None
            current_hunk = None

        elif move_match:
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)
                operations.append(current_op)

            current_op = PatchOperation(
                operation=OperationType.MOVE,
                file_path=move_match.group(1).strip(),
                new_path=move_match.group(2).strip(),
            )
            operations.append(current_op)
            current_op = None
            current_hunk = None

        elif line.startswith("@@"):
            # Context hint / hunk marker
            if current_op:
                if current_hunk and current_hunk.lines:
                    current_op.hunks.append(current_hunk)

                # Extract context hint
                hint_match = re.match(r"@@\s*(.+?)\s*@@", line)
                hint = hint_match.group(1) if hint_match else None
                current_hunk = Hunk(context_hint=hint)

        elif current_op and line:
            # Parse hunk line
            if current_hunk is None:
                current_hunk = Hunk()

            if line.startswith("+"):
                current_hunk.lines.append(HunkLine("+", line[1:]))
            elif line.startswith("-"):
                current_hunk.lines.append(HunkLine("-", line[1:]))
            elif line.startswith(" "):
                current_hunk.lines.append(HunkLine(" ", line[1:]))
            elif line.startswith("\\"):
                # "\ No newline at end of file" marker - skip
                pass
            else:
                # Treat as context line (implicit space prefix)
                current_hunk.lines.append(HunkLine(" ", line))

        i += 1

    # Don't forget the last operation
    if current_op:
        if current_hunk and current_hunk.lines:
            current_op.hunks.append(current_hunk)
        operations.append(current_op)

    # Validate the parsed result
    if not operations:
        # Empty patch is not an error — callers get [] and can decide
        return operations, None

    parse_errors: list[str] = []
    for op in operations:
        if not op.file_path:
            parse_errors.append("Operation with empty file path")
        if op.operation == OperationType.UPDATE and not op.hunks:
            parse_errors.append(f"UPDATE {op.file_path!r}: no hunks found")
        if op.operation == OperationType.MOVE and not op.new_path:
            parse_errors.append(
                f"MOVE {op.file_path!r}: missing destination path (expected 'src -> dst')"
            )

    if parse_errors:
        return [], "Parse error: " + "; ".join(parse_errors)

    return operations, None


def count_occurrences(text: str, pattern: str) -> int:
    """Count non-overlapping occurrences of *pattern* in *text*."""
    count = 0
    start = 0
    while True:
        pos = text.find(pattern, start)
        if pos == -1:
            break
        count += 1
        start = pos + 1
    return count
