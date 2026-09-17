"""Covers every I/O matrix row in the story spec for `bridge_spike.verdict`:
a device class with no result file, the automated `steps` schema (all true
and with one false step), the human-submitted `passed` schema, a malformed
result file, and an automated/build-host file that must never be counted
as a required device-class row -- plus the AD confirmed/provisional rule
and the "a NOT RUN row never shows PASS" rendering guarantee.

Always runs against a `tmp_path` results directory -- never the real
`spikes/bridge/results/`.
"""

from __future__ import annotations

import json
import logging
import re

from bridge_spike import verdict as verdict_module


def _write(results_dir, name, payload) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / name).write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# No result file for a device class -> NOT RUN, never a pass, never omitted.
# ---------------------------------------------------------------------------


def test_missing_result_file_is_not_run_for_every_required_device_class(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)

    for spike_verdict in spike_verdicts:
        assert len(spike_verdict.rows) == len(verdict_module.REQUIRED_DEVICE_CLASSES)
        for row in spike_verdict.rows:
            assert row.verdict == "NOT RUN"
            assert row.verdict != "PASS"
            assert row.ads_confirmed is False
            assert row.fail_branch == verdict_module.NOT_RUN_FAIL_BRANCH
            assert row.source_path is None


def test_not_run_rows_cover_all_four_required_device_class_ids(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    for spike_verdict in spike_verdicts:
        device_classes = {row.device_class for row in spike_verdict.rows}
        assert device_classes == set(verdict_module.REQUIRED_DEVICE_CLASSES)


# ---------------------------------------------------------------------------
# Automated `steps` schema, all true -> PASS, no failing steps, ADs confirmed.
# ---------------------------------------------------------------------------


def test_steps_schema_all_true_scores_pass_with_ads_confirmed(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(
        tmp_path,
        "B1-desktop-chrome.json",
        {"steps": {"trust_ca": True, "passkey": True}, "ok": True},
    )

    verdict, failing_steps, ads_confirmed = verdict_module.score_result_file(tmp_path / "B1-desktop-chrome.json")

    assert verdict == "PASS"
    assert failing_steps == []
    assert ads_confirmed is True


# ---------------------------------------------------------------------------
# Automated `steps` schema, one false -> FAIL, that step named, ADs
# provisional, the spike's own verbatim fail branch shown.
# ---------------------------------------------------------------------------


def test_steps_schema_one_false_scores_fail_and_names_the_step(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(
        tmp_path,
        "B1-android-chrome.json",
        {"steps": {"trust_ca": True, "passkey": False}, "ok": False},
    )

    verdict, failing_steps, ads_confirmed = verdict_module.score_result_file(tmp_path / "B1-android-chrome.json")

    assert verdict == "FAIL"
    assert failing_steps == ["passkey"]
    assert ads_confirmed is False


def test_failing_row_shows_the_spikes_own_verbatim_fail_branch(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(
        tmp_path,
        "B1-android-chrome.json",
        {"steps": {"passkey": False}, "ok": False},
    )

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b1 = next(sv for sv in spike_verdicts if sv.spec.key == "B1")
    row = next(r for r in b1.rows if r.device_class == "android-chrome")

    assert row.verdict == "FAIL"
    assert row.fail_branch == b1.spec.fail_branch
    assert row.fail_branch == (
        "If B1 fails, the owner decides the path at that time (no preset "
        "fallback), given the failing step and device class."
    )


def test_b2_carrier_and_b5_fail_branches_are_the_spines_verbatim_text(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "B2-carrier-desktop-chrome.json", {"steps": {"resume": False}, "ok": False})
    _write(tmp_path, "B5-desktop-chrome.json", {"steps": {"csp": False}, "ok": False})

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b2 = next(sv for sv in spike_verdicts if sv.spec.key == "B2-carrier")
    b5 = next(sv for sv in spike_verdicts if sv.spec.key == "B5")

    b2_row = next(r for r in b2.rows if r.device_class == "desktop-chrome")
    b5_row = next(r for r in b5.rows if r.device_class == "desktop-chrome")

    assert b2_row.fail_branch == (
        "If aioquic interop fails, the carrier is SSE-only until a "
        "maintained WebTransport stack is chosen. A client class that "
        "shows silent gaps uses SSE + POST. The retention default and "
        "budgets are lowered until memory stays bounded."
    )
    assert b5_row.fail_branch == "The failing construct is removed from the build; the policy is never relaxed."


# ---------------------------------------------------------------------------
# Human-submitted `passed` schema (no `steps` key).
# ---------------------------------------------------------------------------


def test_passed_schema_false_scores_fail_and_quotes_the_note(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(
        tmp_path,
        "B1-iphone-ios26.json",
        {"device_class": "iphone-ios26", "passed": False, "note": "passkey prompt never appeared"},
    )

    verdict, failing_steps, ads_confirmed = verdict_module.score_result_file(tmp_path / "B1-iphone-ios26.json")

    assert verdict == "FAIL"
    assert ads_confirmed is False
    assert len(failing_steps) == 1
    assert "no per-step breakdown" in failing_steps[0]
    assert "passkey prompt never appeared" in failing_steps[0]


def test_passed_schema_true_scores_pass(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(
        tmp_path,
        "B1-iphone-ios27.json",
        {"device_class": "iphone-ios27", "passed": True, "note": "all good"},
    )

    verdict, failing_steps, ads_confirmed = verdict_module.score_result_file(tmp_path / "B1-iphone-ios27.json")

    assert verdict == "PASS"
    assert failing_steps == []
    assert ads_confirmed is True


def test_passed_schema_non_boolean_scores_fail_as_malformed(tmp_path) -> None:
    """A `passed` field that isn't a JSON boolean (string/number/null) must
    be named as a malformed-schema failure -- not silently treated as an
    ordinary human-submitted FAIL with the generic note, mirroring how an
    invalid `steps` map is already named as malformed."""
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "B1-desktop-chrome.json", {"device_class": "desktop-chrome", "passed": "yes", "note": "fine"})

    verdict, failing_steps, ads_confirmed = verdict_module.score_result_file(tmp_path / "B1-desktop-chrome.json")

    assert verdict == "FAIL"
    assert ads_confirmed is False
    assert len(failing_steps) == 1
    assert "malformed" in failing_steps[0].lower()
    assert "'passed'" in failing_steps[0]
    assert "no per-step breakdown" not in failing_steps[0]


# ---------------------------------------------------------------------------
# Malformed JSON -> FAIL, parse error named, WARNING logged, never raised
# uncaught and never silently skipped or treated as "not run".
# ---------------------------------------------------------------------------


def test_malformed_json_scores_fail_and_names_the_parse_error(tmp_path, caplog) -> None:
    tmp_path.mkdir(exist_ok=True)
    bad_path = tmp_path / "B1-desktop-chrome.json"
    bad_path.write_text("{not valid json")

    with caplog.at_level(logging.WARNING, logger="bridge_spike.verdict"):
        verdict, failing_steps, ads_confirmed = verdict_module.score_result_file(bad_path)

    assert verdict == "FAIL"
    assert verdict != "NOT RUN"
    assert ads_confirmed is False
    assert len(failing_steps) == 1
    assert "malformed" in failing_steps[0].lower()
    assert any("malformed" in record.message.lower() for record in caplog.records)


def test_malformed_json_row_is_never_reported_as_not_run(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    bad_path = tmp_path / "B1-desktop-chrome.json"
    bad_path.write_text("not json at all")

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b1 = next(sv for sv in spike_verdicts if sv.spec.key == "B1")
    row = next(r for r in b1.rows if r.device_class == "desktop-chrome")

    assert row.verdict == "FAIL"
    assert row.source_path == bad_path


def test_non_utf8_result_file_scores_fail_and_never_raises(tmp_path, caplog) -> None:
    """A non-UTF-8 result file must never crash the whole report --
    `path.read_text()` raises `UnicodeDecodeError`, not `OSError`, and
    that must be caught the same way malformed JSON is."""
    tmp_path.mkdir(exist_ok=True)
    bad_path = tmp_path / "B1-desktop-chrome.json"
    bad_path.write_bytes(b"\xff\xfe\x00\x01not valid utf-8 \x80\x81")

    with caplog.at_level(logging.WARNING, logger="bridge_spike.verdict"):
        verdict, failing_steps, ads_confirmed = verdict_module.score_result_file(bad_path)

    assert verdict == "FAIL"
    assert verdict != "NOT RUN"
    assert ads_confirmed is False
    assert len(failing_steps) == 1
    assert "could not read result file" in failing_steps[0]
    assert any("could not read" in record.message.lower() for record in caplog.records)


def test_non_utf8_result_file_never_crashes_build_epic_verdicts(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    bad_path = tmp_path / "B1-desktop-chrome.json"
    bad_path.write_bytes(b"\xff\xfe\x00\x01not valid utf-8 \x80\x81")

    # Must not raise for this row or for any other spike/device-class row.
    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b1 = next(sv for sv in spike_verdicts if sv.spec.key == "B1")
    row = next(r for r in b1.rows if r.device_class == "desktop-chrome")

    assert row.verdict == "FAIL"
    assert len(spike_verdicts) == 3


def test_non_utf8_automated_evidence_file_is_reported_as_read_error(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    bad_path = tmp_path / "B1-build-host-chromium.json"
    bad_path.write_bytes(b"\xff\xfe\x00\x01not valid utf-8 \x80\x81")

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b1 = next(sv for sv in spike_verdicts if sv.spec.key == "B1")

    assert len(b1.automated_evidence) == 1
    assert b1.automated_evidence[0].ok is None
    assert b1.automated_evidence[0].read_error is not None


def test_unrecognized_schema_scores_fail(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "B1-desktop-chrome.json", {"something_else": True})

    verdict, failing_steps, ads_confirmed = verdict_module.score_result_file(tmp_path / "B1-desktop-chrome.json")

    assert verdict == "FAIL"
    assert ads_confirmed is False
    assert failing_steps


def test_non_dict_json_root_scores_fail_without_raising(tmp_path) -> None:
    """Pins today's `isinstance(data, dict)` guard: a syntactically-valid
    JSON file whose root is a bool/number/null (not an object) must still
    score as an ordinary FAIL, never raise uncaught -- a regression that
    narrowed the guard would otherwise crash the whole report."""
    tmp_path.mkdir(exist_ok=True)
    for non_dict_root in (True, 5, None, "just a string"):
        path = tmp_path / "B1-desktop-chrome.json"
        path.write_text(json.dumps(non_dict_root))

        verdict, failing_steps, ads_confirmed = verdict_module.score_result_file(path)

        assert verdict == "FAIL"
        assert ads_confirmed is False
        assert failing_steps


def test_result_path_is_a_directory_scores_fail_without_raising(tmp_path) -> None:
    """A required-row path that exists but is unreadable for an OS-level
    reason (here: a directory sitting where the file is expected) must be
    caught by the same `except OSError` clause that guards a real
    permission/read failure -- never raised uncaught."""
    tmp_path.mkdir(exist_ok=True)
    candidate = tmp_path / "B1-desktop-chrome.json"
    candidate.mkdir()

    verdict, failing_steps, ads_confirmed = verdict_module.score_result_file(candidate)

    assert verdict == "FAIL"
    assert ads_confirmed is False
    assert failing_steps
    assert "could not read result file" in failing_steps[0]


# ---------------------------------------------------------------------------
# Automated/build-host evidence: surfaced separately, never counted as a
# required device-class row.
# ---------------------------------------------------------------------------


def test_automated_build_host_file_never_counts_as_a_required_row(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "B1-build-host-chromium.json", {"ok": True, "steps": {"x": True}})
    _write(tmp_path, "B1-passkey-desktop-chrome-automated.json", {"ok": False, "steps": {"y": False}})

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b1 = next(sv for sv in spike_verdicts if sv.spec.key == "B1")

    # Neither automated file matches any of the four required device-class
    # ids exactly, so every row must still read NOT RUN.
    assert all(row.verdict == "NOT RUN" for row in b1.rows)
    assert {row.device_class for row in b1.rows} == set(verdict_module.REQUIRED_DEVICE_CLASSES)

    evidence_names = {e.path.name for e in b1.automated_evidence}
    assert evidence_names == {"B1-build-host-chromium.json", "B1-passkey-desktop-chrome-automated.json"}


def test_automated_evidence_reports_its_own_ok_field(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "B5-desktop-chrome-automated.json", {"ok": True, "steps": {"csp": True}})

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b5 = next(sv for sv in spike_verdicts if sv.spec.key == "B5")

    assert len(b5.automated_evidence) == 1
    assert b5.automated_evidence[0].ok is True
    assert b5.automated_evidence[0].read_error is None


def test_automated_evidence_with_no_ok_field_is_not_read_error(tmp_path) -> None:
    """A human-submitted `passed`-schema file swept into automated evidence
    (no `ok` key) parses fine -- it must never be reported as a read/parse
    failure, only as having no recorded `ok` field."""
    tmp_path.mkdir(exist_ok=True)
    _write(
        tmp_path,
        "B1-desktop-chrome-automated.json",
        {"device_class": "desktop-chrome-automated", "passed": True, "note": "fine"},
    )

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b1 = next(sv for sv in spike_verdicts if sv.spec.key == "B1")

    assert len(b1.automated_evidence) == 1
    assert b1.automated_evidence[0].ok is None
    assert b1.automated_evidence[0].read_error is None


def test_automated_evidence_malformed_json_reports_read_error(tmp_path, caplog) -> None:
    tmp_path.mkdir(exist_ok=True)
    bad_path = tmp_path / "B5-build-host-chromium.json"
    bad_path.write_text("{not valid json")

    with caplog.at_level(logging.WARNING, logger="bridge_spike.verdict"):
        spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)

    b5 = next(sv for sv in spike_verdicts if sv.spec.key == "B5")
    assert len(b5.automated_evidence) == 1
    assert b5.automated_evidence[0].ok is None
    assert b5.automated_evidence[0].read_error is not None
    assert any("_read_ok_field" in record.message for record in caplog.records)


def test_required_row_file_is_excluded_from_automated_evidence(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "B1-desktop-chrome.json", {"passed": True, "note": "fine"})
    _write(tmp_path, "B1-build-host-chromium.json", {"ok": True, "steps": {"x": True}})

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b1 = next(sv for sv in spike_verdicts if sv.spec.key == "B1")

    evidence_names = {e.path.name for e in b1.automated_evidence}
    assert "B1-desktop-chrome.json" not in evidence_names
    assert "B1-build-host-chromium.json" in evidence_names

    desktop_row = next(r for r in b1.rows if r.device_class == "desktop-chrome")
    assert desktop_row.verdict == "PASS"
    assert desktop_row.source_path == tmp_path / "B1-desktop-chrome.json"


def test_b2_carrier_glob_never_pulls_in_unrelated_b2_files(tmp_path) -> None:
    """`B2-carrier` results only ever glob `B2-carrier-*.json` -- a
    differently-prefixed B2 file must never be swept into carrier evidence."""
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "B2-carrier-desktop-chrome-automated.json", {"ok": True, "steps": {"x": True}})
    _write(tmp_path, "B2-other-desktop-chrome.json", {"ok": True, "steps": {"x": True}})

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b2 = next(sv for sv in spike_verdicts if sv.spec.key == "B2-carrier")

    evidence_names = {e.path.name for e in b2.automated_evidence}
    assert evidence_names == {"B2-carrier-desktop-chrome-automated.json"}


# ---------------------------------------------------------------------------
# Rendered markdown never shows PASS on a NOT RUN row.
# ---------------------------------------------------------------------------


def _section_for(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"## {heading}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def test_rendered_markdown_never_shows_pass_on_a_not_run_row(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "B1-desktop-chrome.json", {"steps": {"trust_ca": True}, "ok": True})
    # Every other required device class for every spike is left NOT RUN.

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    markdown = verdict_module.render_markdown(spike_verdicts)

    for spike_verdict in spike_verdicts:
        section = _section_for(markdown, spike_verdict.spec.label)
        for row in spike_verdict.rows:
            row_line = next(
                line
                for line in section.splitlines()
                if line.startswith("|") and f"`{row.device_class}`" in line
            )
            cells = [cell.strip() for cell in row_line.strip("|").split("|")]
            assert cells[1] == row.verdict
            if row.verdict == "NOT RUN":
                assert cells[1] != "PASS"
                assert "PASS" not in row_line


def test_rendered_markdown_pass_row_shows_confirmed_ads_and_no_fail_branch_text(tmp_path) -> None:
    """A PASS row's rendered Gating-ADs cell must read exactly `confirmed`
    and its Fail-branch cell must read exactly the placeholder `—` --
    never the spike's own fail-branch text, which only ever belongs on a
    FAIL or NOT RUN row."""
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "B1-desktop-chrome.json", {"steps": {"trust_ca": True}, "ok": True})

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    b1 = next(sv for sv in spike_verdicts if sv.spec.key == "B1")
    pass_row = next(r for r in b1.rows if r.device_class == "desktop-chrome")
    assert pass_row.verdict == "PASS"

    markdown = verdict_module.render_markdown(spike_verdicts)
    section = _section_for(markdown, b1.spec.label)
    row_line = next(
        line for line in section.splitlines() if line.startswith("|") and "`desktop-chrome`" in line
    )
    cells = [cell.strip() for cell in row_line.strip("|").split("|")]

    assert cells[1] == "PASS"
    assert cells[3] == "confirmed"
    assert cells[4] == "—"
    assert b1.spec.fail_branch not in row_line


def test_rendered_markdown_escapes_pipe_and_newline_in_failing_steps(tmp_path) -> None:
    """A failing-step name or human-submitted note containing a literal
    `|` or a newline must never corrupt the generated table: `|` is
    escaped and newlines are collapsed to a space, so the row stays one
    row with exactly five cells."""
    tmp_path.mkdir(exist_ok=True)
    _write(
        tmp_path,
        "B1-desktop-chrome.json",
        {
            "device_class": "desktop-chrome",
            "passed": False,
            "note": "line one | pipe here\nline two breaks the table",
        },
    )

    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    markdown = verdict_module.render_markdown(spike_verdicts)

    b1 = next(sv for sv in spike_verdicts if sv.spec.key == "B1")
    section = _section_for(markdown, b1.spec.label)
    table_lines = [line for line in section.splitlines() if line.startswith("|")]
    row_line = next(line for line in table_lines if "`desktop-chrome`" in line)

    # The escaped note must stay on ONE table row with exactly five cells.
    # A real Markdown parser (and this assertion) treats "\|" as a literal
    # pipe, not a column separator -- only split on an UNESCAPED '|'; a
    # regression that stopped escaping would show up as extra cells here.
    cell_parts = re.split(r"(?<!\\)\|", row_line)
    cells = [cell.strip() for cell in cell_parts[1:-1]]
    assert len(cells) == 5
    assert "line two breaks the table" in row_line
    # The raw (unescaped) pipe must not appear as its own column separator:
    # every '|' in the note is preceded by the escaping backslash.
    assert "line one \\| pipe here" in row_line


def test_rendered_markdown_includes_fail_branch_and_gates_per_spike(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    markdown = verdict_module.render_markdown(spike_verdicts)

    assert "AD-13, AD-14, AD-16, AD-17, AD-19, AD-37" in markdown
    assert "AD-6, AD-9, AD-11, AD-12, AD-31, AD-35, AD-38" in markdown
    assert "AD-36" in markdown
    assert "the owner decides the path at that time" in markdown
    assert "SSE-only until a maintained WebTransport stack is chosen" in markdown
    assert "The failing construct is removed from the build" in markdown


def test_rendered_markdown_never_fabricates_a_section_9_citation_for_b5(tmp_path) -> None:
    tmp_path.mkdir(exist_ok=True)
    spike_verdicts = verdict_module.build_epic_verdicts(tmp_path)
    markdown = verdict_module.render_markdown(spike_verdicts)

    b5_section = _section_for(markdown, "B5")
    b1_section = _section_for(markdown, "B1")
    b2_section = _section_for(markdown, "B2 (carrier half)")

    # B1 and B2 genuinely cite full-picture.md Section 9; B5 must not --
    # it only ever names Section 9 to explicitly disclaim having a row.
    assert "**Reference:** full-picture.md Section 9" in b1_section
    assert "**Reference:** full-picture.md Section 9" in b2_section
    assert "**Reference:** full-picture.md Section 9" not in b5_section
    assert "no full-picture.md Section 9 row for this spike" in b5_section


# ---------------------------------------------------------------------------
# write_verdicts(): None defaults resolved from module globals inside the
# function body, so monkeypatching the module globals is honored.
# ---------------------------------------------------------------------------


def test_write_verdicts_defaults_read_module_globals_at_call_time(tmp_path, monkeypatch) -> None:
    fake_results_dir = tmp_path / "results"
    fake_results_dir.mkdir()
    _write(fake_results_dir, "B1-desktop-chrome.json", {"steps": {"trust_ca": True}, "ok": True})
    fake_output_path = tmp_path / "out" / "epic-1-verdicts.md"

    monkeypatch.setattr(verdict_module, "RESULTS_DIR", fake_results_dir)
    monkeypatch.setattr(verdict_module, "DEFAULT_VERDICTS_PATH", fake_output_path)

    returned_path = verdict_module.write_verdicts()

    assert returned_path == fake_output_path
    assert fake_output_path.exists()
    content = fake_output_path.read_text()
    assert "desktop-chrome" in content
    assert "PASS" in content


def test_write_verdicts_explicit_args_override_defaults(tmp_path) -> None:
    results_dir = tmp_path / "r"
    results_dir.mkdir()
    output_path = tmp_path / "out.md"

    returned_path = verdict_module.write_verdicts(results_dir=results_dir, output_path=output_path)

    assert returned_path == output_path
    assert output_path.exists()
