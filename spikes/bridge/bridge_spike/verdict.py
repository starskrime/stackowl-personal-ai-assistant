"""Turns Stories 1.1-1.5's raw pass/fail result files into a verdict per
spike (B1, the carrier half of B2, B5) and required real-device class
(Story 1.6) -- the epic's one honest evidence document later epics build
on, instead of assumptions.

Scores whatever exists under `results/` against the architecture spine's
own pass criteria (`ARCHITECTURE-SPINE.md` rows 740, 741, 744) and
`full-picture.md` Section 9, and writes
`docs/agentic-os-dashboard/spikes/epic-1-verdicts.md`.

Design notes (see the story spec for the full rationale):

- The four required device classes are a fixed registry, not discovered
  from disk -- an open-ended scheme would let a typo'd `device_class`
  string silently masquerade as a required row. Any other
  `{spike}-*.json` file under `results/` is still surfaced, just as
  separate automated/build-host evidence, so nothing on disk is ever
  hidden from the report.
- `write_verdicts()`'s `results_dir`/`output_path` parameters default to
  `None` and are resolved from the `RESULTS_DIR`/`DEFAULT_VERDICTS_PATH`
  module globals *inside the function body*, not baked into the
  signature at import time -- so `monkeypatch.setattr(verdict_module,
  "RESULTS_DIR", tmp_path)` works the same way `tests/test_server.py`
  already patches `RESULTS_DIR` for `_handle_submit_result`.
- B5 has no `full-picture.md` Section 9 row (only B1 and B2 do); the
  report cites the spine alone for B5 rather than fabricating a Section 9
  citation that doesn't exist.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .server import RESULTS_DIR

logger = logging.getLogger("bridge_spike.verdict")

# Repo root: spikes/bridge/bridge_spike/verdict.py -> bridge_spike -> bridge
# -> spikes -> repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_VERDICTS_PATH = _REPO_ROOT / "docs" / "agentic-os-dashboard" / "spikes" / "epic-1-verdicts.md"

# Required device classes (epic-1-context.md:21, verbatim list): "stock
# iPhone iOS 26.4+ and iOS 27, Android Chrome 148+, desktop Chrome". Exactly
# these four ids, in this order, for every spike -- never discovered from
# disk (see module docstring).
REQUIRED_DEVICE_CLASSES: tuple[str, ...] = (
    "iphone-ios26",
    "iphone-ios27",
    "android-chrome",
    "desktop-chrome",
)

DEVICE_CLASS_LABELS: dict[str, str] = {
    "iphone-ios26": "stock iPhone, iOS 26.4+, Safari tab and installed app",
    "iphone-ios27": "stock iPhone, iOS 27, Safari tab and installed app",
    "android-chrome": "Android, current Chrome stable 148+",
    "desktop-chrome": "desktop Chrome",
}

NOT_RUN_FAIL_BRANCH = "n/a -- not run"


@dataclass(frozen=True)
class SpikeSpec:
    """One spike's gate, exactly as the architecture spine states it."""

    key: str  # results/ filename prefix, e.g. "B1", "B2-carrier", "B5"
    label: str  # display name for the report
    gates: tuple[str, ...]  # gating AD ids, verbatim (ARCHITECTURE-SPINE.md)
    fail_branch: str  # verbatim fail-branch text (ARCHITECTURE-SPINE.md)
    full_picture_ref: str | None  # full-picture.md Section 9 citation, or None


# Gates and fail-branch text below are ARCHITECTURE-SPINE.md's own verbatim
# wording (rows 740, 741, 744) -- never paraphrased narrower or broader.
SPIKE_SPECS: tuple[SpikeSpec, ...] = (
    SpikeSpec(
        key="B1",
        label="B1",
        gates=("AD-13", "AD-14", "AD-16", "AD-17", "AD-19", "AD-37"),
        fail_branch=(
            "If B1 fails, the owner decides the path at that time (no preset "
            "fallback), given the failing step and device class."
        ),
        full_picture_ref="full-picture.md Section 9, B1 row",
    ),
    SpikeSpec(
        key="B2-carrier",
        label="B2 (carrier half)",
        gates=("AD-6", "AD-9", "AD-11", "AD-12", "AD-31", "AD-35", "AD-38"),
        fail_branch=(
            "If aioquic interop fails, the carrier is SSE-only until a "
            "maintained WebTransport stack is chosen. A client class that "
            "shows silent gaps uses SSE + POST. The retention default and "
            "budgets are lowered until memory stays bounded."
        ),
        full_picture_ref="full-picture.md Section 9, B2 row",
    ),
    SpikeSpec(
        key="B5",
        label="B5",
        gates=("AD-36",),
        fail_branch="The failing construct is removed from the build; the policy is never relaxed.",
        full_picture_ref=None,
    ),
)


@dataclass
class DeviceClassRow:
    """One spike's verdict for one required device class."""

    device_class: str
    verdict: str  # "PASS" | "FAIL" | "NOT RUN"
    failing_steps: list[str]
    ads_confirmed: bool  # True only on a full pass of this row's own file
    fail_branch: str | None  # None only for PASS rows
    source_path: Path | None


@dataclass
class AutomatedEvidence:
    """One automated/build-host result file -- a prerequisite, never a
    required device-class row."""

    path: Path
    ok: bool | None  # best-effort read of the file's own "ok" field; None if absent
    read_error: str | None  # set only when the file could not be read/parsed at all


@dataclass
class SpikeVerdict:
    spec: SpikeSpec
    rows: list[DeviceClassRow] = field(default_factory=list)
    automated_evidence: list[AutomatedEvidence] = field(default_factory=list)


def score_result_file(path: Path) -> tuple[str, list[str], bool]:
    """Scores one required-device-class result file against its own
    recorded fields only -- never infers or fabricates a verdict the
    file's own data doesn't support. Returns (verdict, failing_steps,
    ads_confirmed). A malformed or unrecognized file is reported as a
    failure with the parsing problem named -- never silently skipped and
    never treated as a pass or as "not run"."""
    logger.debug("bridge_spike.verdict: score_result_file entry -- path=%s", path)

    try:
        raw = path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("bridge_spike.verdict: score_result_file -- could not read %s: %s", path, exc)
        return "FAIL", [f"could not read result file: {exc}"], False

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("bridge_spike.verdict: score_result_file -- malformed JSON in %s: %s", path, exc)
        return "FAIL", [f"malformed result file (invalid JSON): {exc}"], False

    if not isinstance(data, dict):
        logger.warning("bridge_spike.verdict: score_result_file -- %s root is not a JSON object", path)
        return "FAIL", ["malformed result file: root is not a JSON object"], False

    logger.debug("bridge_spike.verdict: score_result_file step -- %s parsed as a JSON object", path)

    if "steps" in data:
        logger.debug("bridge_spike.verdict: score_result_file decision -- %s uses the 'steps' schema", path)
        steps = data.get("steps")
        if not isinstance(steps, dict) or not steps:
            logger.warning("bridge_spike.verdict: score_result_file -- %s has an empty/invalid 'steps' map", path)
            return "FAIL", ["malformed result file: 'steps' is missing, empty, or not an object"], False
        failing_steps = [name for name, ok in steps.items() if ok is not True]
        if failing_steps:
            logger.warning(
                "bridge_spike.verdict: score_result_file exit -- %s FAIL (%d failing step(s))",
                path,
                len(failing_steps),
            )
            return "FAIL", failing_steps, False
        logger.debug("bridge_spike.verdict: score_result_file exit -- %s PASS", path)
        return "PASS", [], True

    if "passed" in data:
        logger.debug("bridge_spike.verdict: score_result_file decision -- %s uses the 'passed' schema", path)
        passed_value = data.get("passed")
        if not isinstance(passed_value, bool):
            logger.warning(
                "bridge_spike.verdict: score_result_file -- %s has a non-boolean 'passed' field: %r",
                path,
                passed_value,
            )
            return "FAIL", [f"malformed result file: 'passed' is not a boolean (got {passed_value!r})"], False
        if passed_value is True:
            logger.debug("bridge_spike.verdict: score_result_file exit -- %s PASS", path)
            return "PASS", [], True
        note = data.get("note")
        if isinstance(note, str) and note.strip():
            failing_note = f'no per-step breakdown (human-submitted); note: "{note}"'
        else:
            failing_note = "no per-step breakdown (human-submitted); no note given"
        logger.warning("bridge_spike.verdict: score_result_file exit -- %s FAIL (human-submitted)", path)
        return "FAIL", [failing_note], False

    logger.warning("bridge_spike.verdict: score_result_file -- %s has neither 'steps' nor 'passed'", path)
    return "FAIL", ["malformed result file: neither 'steps' nor 'passed' field present"], False


def _read_ok_field(path: Path) -> tuple[bool | None, str | None]:
    """Best-effort read of an automated/build-host file's own "ok" field,
    for the report's informational listing. Never raised uncaught: an
    unreadable/malformed file logs a warning and reads as (None, reason)
    rather than crashing the whole report; a file with no "ok" field
    (e.g. the human-submitted `passed` schema) reads as (None, None)."""
    logger.debug("bridge_spike.verdict: _read_ok_field entry -- path=%s", path)

    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("bridge_spike.verdict: _read_ok_field -- could not read/parse %s: %s", path, exc)
        return None, str(exc)

    logger.debug("bridge_spike.verdict: _read_ok_field step -- %s read and parsed as JSON", path)

    if not isinstance(data, dict):
        logger.warning("bridge_spike.verdict: _read_ok_field -- %s root is not a JSON object", path)
        return None, "root is not a JSON object"

    ok = data.get("ok")
    if isinstance(ok, bool):
        logger.debug("bridge_spike.verdict: _read_ok_field decision -- %s has a boolean 'ok'=%s", path, ok)
        logger.debug("bridge_spike.verdict: _read_ok_field exit -- returning ok=%s, read_error=None", ok)
        return ok, None
    logger.debug("bridge_spike.verdict: _read_ok_field decision -- %s has no boolean 'ok' field", path)
    logger.debug("bridge_spike.verdict: _read_ok_field exit -- returning ok=None, read_error=None")
    return None, None


def build_epic_verdicts(results_dir: Path) -> list[SpikeVerdict]:
    """Scans `results_dir` for each spike in `SPIKE_SPECS` and scores every
    required device class -- a class with no matching result file is
    reported NOT RUN, never a pass and never silently omitted. Any other
    `{spike}-*.json` file already under `results_dir` (the automated/
    build-host check files Stories 1.1-1.5 produced) is collected
    separately as automated/build-host evidence, never counted as
    satisfying a required device-class row."""
    logger.debug("bridge_spike.verdict: build_epic_verdicts entry -- results_dir=%s", results_dir)

    spike_verdicts: list[SpikeVerdict] = []
    for spec in SPIKE_SPECS:
        logger.debug("bridge_spike.verdict: build_epic_verdicts step -- scoring spike %s", spec.key)
        rows: list[DeviceClassRow] = []
        matched_paths: set[Path] = set()

        for device_class in REQUIRED_DEVICE_CLASSES:
            candidate = results_dir / f"{spec.key}-{device_class}.json"
            if not candidate.exists():
                logger.debug(
                    "bridge_spike.verdict: build_epic_verdicts decision -- %s/%s NOT RUN (no result file)",
                    spec.key,
                    device_class,
                )
                rows.append(
                    DeviceClassRow(
                        device_class=device_class,
                        verdict="NOT RUN",
                        failing_steps=[],
                        ads_confirmed=False,
                        fail_branch=NOT_RUN_FAIL_BRANCH,
                        source_path=None,
                    )
                )
                continue

            matched_paths.add(candidate)
            verdict, failing_steps, ads_confirmed = score_result_file(candidate)
            rows.append(
                DeviceClassRow(
                    device_class=device_class,
                    verdict=verdict,
                    failing_steps=failing_steps,
                    ads_confirmed=ads_confirmed,
                    fail_branch=None if verdict == "PASS" else spec.fail_branch,
                    source_path=candidate,
                )
            )

        automated_evidence = []
        for candidate_path in sorted(results_dir.glob(f"{spec.key}-*.json")):
            if candidate_path in matched_paths:
                continue
            ok, read_error = _read_ok_field(candidate_path)
            automated_evidence.append(AutomatedEvidence(path=candidate_path, ok=ok, read_error=read_error))

        logger.debug(
            "bridge_spike.verdict: build_epic_verdicts exit -- spike=%s rows=%d automated_evidence=%d",
            spec.key,
            len(rows),
            len(automated_evidence),
        )
        spike_verdicts.append(SpikeVerdict(spec=spec, rows=rows, automated_evidence=automated_evidence))

    logger.debug("bridge_spike.verdict: build_epic_verdicts exit -- %d spike(s) scored", len(spike_verdicts))
    return spike_verdicts


def _render_cell(value: str) -> str:
    return value if value else "—"


def _escape_table_cell(text: str) -> str:
    """Escapes one piece of externally-sourced text (a failing-step name,
    or a human-submitted note embedded in one) for safe embedding in a
    single Markdown table cell: newlines are collapsed to a space so the
    row can never split across lines, and a literal `|` is escaped so it
    can never be read as a column separator and corrupt the table."""
    collapsed = " ".join(text.splitlines())
    return collapsed.replace("|", "\\|")


def render_markdown(spike_verdicts: list[SpikeVerdict]) -> str:
    """Renders the epic verdicts report. A NOT RUN row never shows PASS."""
    logger.debug("bridge_spike.verdict: render_markdown entry -- %d spike(s)", len(spike_verdicts))

    lines: list[str] = [
        "# Epic 1 verdicts",
        "",
        "Generated by `uv run spikes/bridge/kit.py verdict`. Scores "
        "`spikes/bridge/results/` against the architecture spine's spike "
        "gates (`docs/agentic-os-dashboard/architecture/ARCHITECTURE-SPINE.md`, "
        "rows 740, 741, 744) and `docs/agentic-os-dashboard/full-picture.md` "
        "Section 9. A device class with no result file is reported as "
        "**NOT RUN**, never as a pass.",
        "",
        "This report is the epic's single evidence document: later epics "
        "build on these verdicts, not on assumptions.",
        "",
    ]

    for spike_verdict in spike_verdicts:
        spec = spike_verdict.spec
        lines.append(f"## {spec.label}")
        lines.append("")
        lines.append(f"**Gating ADs:** {', '.join(spec.gates)}")
        if spec.full_picture_ref is not None:
            logger.debug("bridge_spike.verdict: render_markdown decision -- %s cites full_picture_ref", spec.key)
            lines.append(f"**Reference:** {spec.full_picture_ref}; ARCHITECTURE-SPINE.md spike gates table")
        else:
            logger.debug(
                "bridge_spike.verdict: render_markdown decision -- %s has no full-picture.md Section 9 row",
                spec.key,
            )
            lines.append(
                "**Reference:** ARCHITECTURE-SPINE.md spike gates table "
                "(no full-picture.md Section 9 row for this spike)"
            )
        lines.append(f"**Fail branch:** {spec.fail_branch}")
        lines.append("")
        lines.append("| Device class | Verdict | Failing steps | Gating ADs | Fail branch |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in spike_verdict.rows:
            device_label = f"`{row.device_class}` ({DEVICE_CLASS_LABELS[row.device_class]})"
            failing_cell = _render_cell("; ".join(_escape_table_cell(step) for step in row.failing_steps))
            ads_cell = "confirmed" if row.ads_confirmed else "provisional"
            fail_branch_cell = _render_cell(row.fail_branch or "")
            lines.append(f"| {device_label} | {row.verdict} | {failing_cell} | {ads_cell} | {fail_branch_cell} |")
        lines.append("")
        logger.debug(
            "bridge_spike.verdict: render_markdown step -- %s table rendered (%d row(s))",
            spec.key,
            len(spike_verdict.rows),
        )

        lines.append(
            f"**Automated / build-host evidence for {spec.label}** "
            "(prerequisite only -- never a required device-class row):"
        )
        lines.append("")
        if spike_verdict.automated_evidence:
            for evidence in spike_verdict.automated_evidence:
                if evidence.read_error is not None:
                    ok_label = f"could not read/parse: {evidence.read_error}"
                elif evidence.ok is True:
                    ok_label = "ok"
                elif evidence.ok is False:
                    ok_label = "FAILED"
                else:
                    ok_label = "no 'ok' field recorded"
                lines.append(f"- `{evidence.path.name}` -- {ok_label}")
        else:
            lines.append("- (none present)")
        lines.append("")

    logger.debug("bridge_spike.verdict: render_markdown exit -- %d line(s)", len(lines))
    return "\n".join(lines) + "\n"


def write_verdicts(results_dir: Path | None = None, output_path: Path | None = None) -> Path:
    """Scores `results_dir` (default: the module-level `RESULTS_DIR`,
    resolved here so a test's `monkeypatch.setattr(verdict, "RESULTS_DIR",
    tmp_path)` is honored) and writes the rendered report to `output_path`
    (default: the module-level `DEFAULT_VERDICTS_PATH`, resolved the same
    way). Returns the path written."""
    resolved_results_dir = results_dir if results_dir is not None else RESULTS_DIR
    resolved_output_path = output_path if output_path is not None else DEFAULT_VERDICTS_PATH
    logger.debug(
        "bridge_spike.verdict: write_verdicts entry -- results_dir=%s output_path=%s",
        resolved_results_dir,
        resolved_output_path,
    )
    logger.debug(
        "bridge_spike.verdict: write_verdicts decision -- results_dir is %s, output_path is %s",
        "explicit" if results_dir is not None else "the default RESULTS_DIR",
        "explicit" if output_path is not None else "the default DEFAULT_VERDICTS_PATH",
    )

    spike_verdicts = build_epic_verdicts(resolved_results_dir)
    markdown = render_markdown(spike_verdicts)
    logger.debug(
        "bridge_spike.verdict: write_verdicts step -- rendered %d byte(s) of markdown, writing to %s",
        len(markdown),
        resolved_output_path,
    )

    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(markdown)

    logger.debug("bridge_spike.verdict: write_verdicts exit -- wrote %s", resolved_output_path)
    return resolved_output_path
