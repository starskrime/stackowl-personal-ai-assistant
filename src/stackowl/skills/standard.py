"""The skill authoring standard — D10.2.

THE SINGLE MACHINE-READABLE SOURCE (invariant I8). The design document cites
these constants and :func:`describe_for_prompt` generates the text the authoring
prompt carries, so a rule exists in exactly one place. Three hand-maintained
copies of a standard — prose, prompt, validator — is how a standard rots.

WHY IT EXISTS. Measured 2026-08-05: 407 learned skills, only 142 distinct base
names. 265 were numbered duplicates and one lesson had been written twenty-one
times, because ``_cluster_already_covered`` deduped on *evidence* (parent trace
ids) rather than on the conclusion, so a lesson re-derived from a new incident
always looked new and the synthesizer appended ``-N`` and wrote it again.

TAUGHT, THEN CHECKED. The authoring prompt states these rules and the validator
is the backstop for when the model ignores them. Validation alone would make
every non-conforming synthesis pay a wasted LLM call on a reject-retry.

DIVERGENCE FROM THE REFERENCE PLATFORM, stated because a future reader will
otherwise "fix" it back: their rubric is enforced by human reviewers. Ours has
to hold when the AGENT is the author, so every rule here is machine-checkable —
a convention the writer cannot read is not a rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ALLOWED_SUPPORT_DIRS",
    "MAX_DESCRIPTION_CHARS",
    "REQUIRED_FRONTMATTER",
    "REQUIRED_SECTIONS",
    "SOFT_MAX_LINES",
    "STANDARD_VERSION",
    "Violation",
    "base_name",
    "canonical_key",
    "blocking",
    "describe_for_prompt",
    "validate_body",
    "validate_frontmatter",
    "validate_name",
    "validate_support_dirs",
]

#: Bumped when a rule gets STRICTER. Each skill records the version it conformed
#: to, so a later change re-migrates only what actually moved (R6Q24) rather than
#: re-validating everything or grandfathering old skills into fragmentation.
#:
#: NOT bumped when a rule LOOSENS. Everything that passed the stricter version
#: still passes, so a bump would only spend one LLM call per skill to reach the
#: same catalog. The shell-verb fix — prose no longer rejected — is exactly this
#: case and deliberately left at 1.
STANDARD_VERSION = 1

#: One sentence, at a glance. Adopted from the reference platform AGAINST our own
#: measured evidence — our median is 197 chars and only 15 of 423 skills would
#: pass — on the operator's explicit decision (D10.2 R1Q2). The retrieval signal
#: that cap removes is recovered by making ``when_to_use`` a required rich field:
#: the embedder composes name + description + when_to_use + body, and skills_fts
#: indexes when_to_use too, so the ~137 stripped characters move rather than go.
MAX_DESCRIPTION_CHARS = 60

#: Everything else is derived (source, enabled, version) or optional. Requiring
#: more would only make the agent invent values to satisfy a validator.
REQUIRED_FRONTMATTER: tuple[str, ...] = ("name", "description", "when_to_use")

#: Fixed order, adopted wholesale from the reference platform. Supersedes the
#: three-section template chosen earlier in D09.3 R2Q5 — "Steps" is "Procedure"
#: here and "When not to use" folds into "When to Use" as its negative case.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "When to Use",
    "Prerequisites",
    "How to Run",
    "Quick Reference",
    "Procedure",
    "Pitfalls",
    "Verification",
)

#: A skill may only carry these subdirectories. Gives consolidation the
#: predictable package shape it needs to re-home support files instead of
#: flattening a SKILL.md away from the scripts it references.
ALLOWED_SUPPORT_DIRS: frozenset[str] = frozenset({"scripts", "references", "templates"})

#: A long skill is a smell, not an error — a hard cap would reject a genuinely
#: detailed procedure. Warn only.
SOFT_MAX_LINES = 200

#: Any run of non-alphanumerics separates words — hyphen, underscore, space, dot.
#: `\w` would NOT do: it INCLUDES the underscore, so `a_b_c` arrives as one token
#: while `a-b-c` splits into three. That exact mistake made my own duplicate
#: measurement read 35% when the truth was 60%, and hid three of the six
#: identical-token families outright.
_CANONICAL_SPLIT_RE = re.compile(r"[^a-z0-9]+")

#: The ``-N`` suffix existed for exactly one reason: the synthesizer needed a
#: free directory name. Forbidding it makes a collision LOUD, so the writer must
#: reinforce the existing skill or choose a genuinely different name.
_NUMBERED_SUFFIX_RE = re.compile(r"-\d+$")

#: A markdown ATX heading, any level.
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)

#: A backticked token shaped like a tool name (snake_case, no spaces). Deliberately
#: narrow: prose in backticks (`~/.stackowl`, `foo.py`) must not be mistaken for a
#: tool reference and rejected.
_BACKTICK_TOKEN_RE = re.compile(r"`([a-z][a-z0-9_]{2,})`")

#: Raw shell verbs a skill should never instruct the agent to reach for when a
#: real capability exists.
#:
#: MATCHED ONLY INSIDE CODE, never in prose. The first version of this rule
#: matched bare words anywhere and rejected "Use this to find the failing job"
#: — a hardcoded English word-list applied to natural language, which is the
#: standing rule this codebase already has against keyword lists. "find", "cat"
#: and "ls" are ordinary English; `find . -name x` is a shell instruction. The
#: difference is structural (is it code?), not lexical, so that is what we test.
#: Anchored to a COMMAND POSITION: the start of a line, or just after a pipe /
#: separator. ``re.MULTILINE`` so each line of a fenced block is a candidate.
_SHELL_VERBS_RE = re.compile(
    r"(?:^|[|;&(]\s*)(grep|sed|awk|cat|ls|find)\b", re.MULTILINE,
)

#: A fenced code block, or an inline code span. The CONTENT is captured, not the
#: delimiters — leaving the backticks in would push the first token off the start
#: of the line and defeat the command-position anchor above. Everything outside
#: these is prose and is not subject to the shell-verb rule.
_CODE_SPAN_RE = re.compile(r"```[a-z]*\n?(.*?)```|`([^`\n]+)`", re.DOTALL)

#: The same verbs as a plain set, for de-duplicating against the tool-name rule.
_SHELL_VERB_NAMES = frozenset({"grep", "sed", "awk", "cat", "ls", "find"})


@dataclass(frozen=True)
class Violation:
    """One broken rule.

    ``blocking`` distinguishes a rejection from a warning: the soft line cap
    reports without refusing, because "too detailed" is not the validator's
    judgement to make.
    """

    rule: str
    detail: str
    blocking: bool = True

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


def canonical_key(name: str) -> str:
    """Same words in any arrangement -> one key. THE ONE COPY, beside base_name.

    ESC-52, decided by Bakir 2026-08-24. `base_name` answers "is this a `-N`
    variant of something we know?" and nothing answered "is this the same NAME,
    rearranged?" — so the corpus grew families that are obviously identical to a
    human and invisible to the gate.

    MEASURED 2026-08-24 across 171 non-archived skills: 168 near-duplicate pairs
    covering 102 skills, and SIX families sharing an identical token set::

        download-instagram-video          / instagram-video-download
        incident-evidence-brief           / incident_evidence_brief
        check-stock-price-today           / check_stock_price_today
        memory_unachieved_effect_fallback / unachieved_effect_memory_fallback
        report-task-status                / task-status-report
        check-stock-price-alert           / stock-price-alert-check

    Three of those six differ ONLY by separator, and the last one was minted by
    the synthesizer at 08:33 that morning — hours after the fix that revived it.

    TWO BLIND SPOTS, ONE KEY. Splitting on any non-alphanumeric run catches the
    separator variants; SORTING the tokens catches the word-order permutations.
    `base_name` is applied first so `foo-2` and `foo` still collapse together —
    the two rules compose rather than compete, and neither is a copy of the other.

    DELIBERATELY NOT FUZZY, for the reason the memory canonicaliser was not: a
    near-miss would merge two skills that are genuinely different, and a wrong
    merge corrupts a reader where a duplicate only wastes a row.
    """
    tokens = sorted(t for t in _CANONICAL_SPLIT_RE.split(base_name(name).lower()) if t)
    return " ".join(tokens)


def base_name(name: str) -> str:
    """Strip a ``-N`` disambiguation suffix, so ``foo-3`` answers "do we already
    know foo?".

    THE ONE COPY (I8). This regex previously existed three times — here, as
    ``synthesizer._NAME_SUFFIX_RE``, and inline in the consolidation planner.
    The rule about what a name may be belongs to the module that defines the
    rule; three copies is how ``-N`` stops being forbidden in one of them.

    Anchored and digits-only, so a skill legitimately named ``http-2`` collapses
    onto ``http``. That is the accepted side of the trade: the measured cost of
    NOT collapsing was 269 duplicate rows across 43 families.
    """
    return _NUMBERED_SUFFIX_RE.sub("", name)


def validate_name(name: str) -> list[Violation]:
    """Name rules. Returns every violation, never just the first (R6Q22)."""
    out: list[Violation] = []
    if not name:
        out.append(Violation("name", "required"))
        return out
    if _NUMBERED_SUFFIX_RE.search(name):
        out.append(Violation(
            "name",
            f"'{name}' ends in a numeric suffix — forbidden. If the base skill "
            f"already exists, reinforce it instead of writing a variant; "
            f"otherwise choose a genuinely different name.",
        ))
    return out


def validate_frontmatter(fields: dict[str, object]) -> list[Violation]:
    """Frontmatter rules: required fields present, description short and single.

    Takes a plain dict rather than a SkillManifest so the validator has no
    dependency on the model — the migration validates parsed YAML that may not
    construct a manifest at all, and a validator that can only inspect
    already-valid objects cannot report why something failed to become one.
    """
    out: list[Violation] = []
    for field in REQUIRED_FRONTMATTER:
        value = fields.get(field)
        if not isinstance(value, str) or not value.strip():
            out.append(Violation(field, "required and must be non-empty"))

    description = fields.get("description")
    if isinstance(description, str) and description.strip():
        text = description.strip()
        if len(text) > MAX_DESCRIPTION_CHARS:
            out.append(Violation(
                "description",
                f"{len(text)} characters exceeds the {MAX_DESCRIPTION_CHARS}-character "
                f"limit — it is a one-line label, not the explanation. Put the "
                f"detail in when_to_use, which is what retrieval reads.",
            ))
        # "One sentence" checked as: no sentence-ending punctuation followed by
        # more prose. A trailing full stop is fine.
        if re.search(r"[.?!]\s+\S", text):
            out.append(Violation(
                "description",
                "must be a single sentence",
            ))
    return out


def validate_body(
    body: str,
    *,
    known_tools: frozenset[str] | None = None,
) -> list[Violation]:
    """Body rules: required sections in order, tool names, soft length cap.

    ``known_tools`` is the live registry's tool names. When it is ``None`` the
    tool-name rule is SKIPPED rather than failed — a registry that could not be
    consulted must never block authoring (see the design's Failure modes).
    """
    out: list[Violation] = []
    headings = [h.strip() for h in _HEADING_RE.findall(body)]

    missing = [s for s in REQUIRED_SECTIONS if s not in headings]
    if missing:
        out.append(Violation(
            "sections",
            f"missing required section(s): {', '.join(missing)}. Required order: "
            + " -> ".join(REQUIRED_SECTIONS),
        ))

    # Order is only checked among the sections that ARE present, so a missing
    # section is reported once (above) rather than also as an ordering error.
    present = [h for h in headings if h in REQUIRED_SECTIONS]
    expected = [s for s in REQUIRED_SECTIONS if s in present]
    if present != expected:
        out.append(Violation(
            "section_order",
            f"sections out of order: {present} — required order is {expected}",
        ))

    if known_tools is not None:
        referenced = set(_BACKTICK_TOKEN_RE.findall(body))
        # A backticked shell verb gets the shell_verbs message below, which says
        # something useful. Reporting it here too is two errors for one mistake.
        unknown = sorted(
            t for t in referenced
            if t not in known_tools and t not in _SHELL_VERB_NAMES
        )
        if unknown:
            out.append(Violation(
                "tool_names",
                f"backticked token(s) that are not registered tools: "
                f"{', '.join(unknown)}. Reference capabilities by their real names.",
            ))

    # Only the CODE in the document, joined. A verb in prose is a verb.
    code = "\n".join(
        span for match in _CODE_SPAN_RE.findall(body) for span in match if span
    )
    shell = sorted(set(_SHELL_VERBS_RE.findall(code)))
    if shell:
        out.append(Violation(
            "shell_verbs",
            f"instructs raw shell ({', '.join(shell)}) — point at the real "
            f"capability instead so the skill does not teach shelling out.",
        ))

    line_count = len(body.splitlines())
    if line_count > SOFT_MAX_LINES:
        out.append(Violation(
            "length",
            f"{line_count} lines exceeds the soft cap of {SOFT_MAX_LINES}",
            blocking=False,
        ))
    return out


def validate_support_dirs(subdirectories: list[str]) -> list[Violation]:
    """Only ``scripts/``, ``references/`` and ``templates/`` are permitted."""
    bad = sorted(d for d in subdirectories if d not in ALLOWED_SUPPORT_DIRS)
    if not bad:
        return []
    return [Violation(
        "support_dirs",
        f"unexpected subdirectory/ies: {', '.join(bad)}. Only "
        f"{', '.join(sorted(ALLOWED_SUPPORT_DIRS))} are permitted.",
    )]


def blocking(violations: list[Violation]) -> list[Violation]:
    """The subset that must refuse a write."""
    return [v for v in violations if v.blocking]


def describe_for_prompt() -> str:
    """The standard, as prose for the authoring prompt.

    GENERATED FROM THE CONSTANTS ABOVE (I8). If a rule changes, this text changes
    with it — the alternative is a prompt that teaches a standard the validator
    no longer enforces, which is worse than not teaching it at all.
    """
    sections = "\n".join(f"  {i + 1}. ## {s}" for i, s in enumerate(REQUIRED_SECTIONS))
    return (
        "SKILL AUTHORING STANDARD (a write that breaks these is rejected):\n"
        f"- Frontmatter must include: {', '.join(REQUIRED_FRONTMATTER)}.\n"
        f"- `description` is ONE sentence, at most {MAX_DESCRIPTION_CHARS} characters. "
        "COUNT the characters after you write it; if it is over, cut it down before "
        "saving — do not ship a sentence and hope.\n"
        "- `when_to_use` is 1-3 sentences and carries the retrieval signal — say "
        "when someone should reach for this, and when they should not.\n"
        "- The name must NOT end in a number. If a skill with that name already "
        "exists, reinforce it rather than writing a variant.\n"
        "- The body has exactly these sections, in this order:\n"
        f"{sections}\n"
        "- Reference capabilities by their real registered names in backticks. "
        "Never instruct raw shell where a capability exists.\n"
        f"- Support files go in {'/, '.join(sorted(ALLOWED_SUPPORT_DIRS))}/ only.\n"
        f"- Keep it under about {SOFT_MAX_LINES} lines."
    )
