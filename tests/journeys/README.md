# Business-requirement journey tests (`tests/journeys/`)

Each test here = one **PRD User Journey** (`_bmad-output/planning-artifacts/prd-tool-expansion.md` §3),
driven from the **gateway** (real Telegram adapter → scanner → `AsyncioBackend` → real services),
mocking **ONLY the AI provider** (+ the bot HTTP transport at the network edge). Assertions check the
**user OUTCOME** ("the reminder will actually fire", "the user can recall what was researched"), NOT a
tool's return shape. Cross-feature journeys prove the shipped features actually work *together* — the
gap a per-tool smoke can't catch. See `[[feedback_gateway_integration_tests]]`.

A journey test that fails because a feature isn't wired end-to-end is a **real finding → STOP + inform**
(`[[feedback_no_silent_integration_fix]]`); never patch the test to hide it.

## Coverage matrix (J1–J10)

| Journey | User outcome | Tools / epics | Status | Test |
|---------|--------------|---------------|--------|------|
| **J1** | Summarize a PDF now + a reminder that will actually fire Friday + confirmation | pdf (E3) → cronjob (E7) → send_message (E7) | ✅ **live** | `test_j1_pdf_summarize_and_schedule.py` |
| **J2** | Research the web once, then recall the findings later without re-searching | web_search (E6) + web_fetch (E2) → memory (E4) | ✅ **live** | `test_j2_research_and_remember.py` |
| **J3** | Report a bug, the agent locates + fixes it on disk (run-to-confirm when E11 ships) | search_files/read_file/edit/apply_patch (E3); execute_code (E11) | ✅ **live arc** (fix-on-disk green; exec reproduce/confirm `skip` until E11) | `test_j3_debug_script.py` |
| **J4** | Operate a no-API desktop app via screenshot + vision + input | computer_use (E12) + vision_analyze (E10) | ⛔ deferred (E10/E12 not shipped) | — |
| **J5** | Get independent opinions on a hard decision, with dissent surfaced | mixture_of_agents (E8) | ✅ **live** | `test_j5_independent_opinions.py` |
| **J6** | Ambiguous request → agent asks → user answers → same turn resumes | clarify (E5) | ✅ **live** | `test_j6_clarify_pause_resume.py` |
| **J7** | Control smart-home devices (with confirmation) | ha_call_service (E13) | ⛔ deferred (E13 not shipped) | — |
| **J8** | Plan N consequential actions, approve as ONE batch, execute audited | batch_approve (J8) → cronjob/send_message (E7) | ✅ **live** | `test_j8_batch_approval.py` |
| **J9** | Run a longer task, WAIT for it to finish (no busy-poll), then read its output | process (E9) → wait (E9) → process log | ✅ **live** | `test_j9_process_wait.py` |
| **J10** | Reference an image; the owl describes what's in it (cloud egress disclosed) | vision_analyze (E10) — local-first; egress note iff cloud | ✅ **live** | `test_j10_vision_analyze.py` |
| **J11** | Run code and get the real result; host untouched; net denied; declinable | execute_code (E11) → SandboxSelector → REAL bwrap sandbox | ✅ **live** (live run/net SKIP if no bwrap; consent/child/unavailable always run) | `test_j11_execute_code.py` |

**live** = green end-to-end now · **partial** = shippable arc tested, unshipped steps `pytest.mark.skip` ·
**deferred** = epic not shipped; this journey becomes that epic's acceptance test when it lands (no silent gap).
