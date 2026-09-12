"""One route's outcome is not the page's outcome — proven by RUNNING the page.

Every other guard on this page reads its source. That is right for "does it
contain an f-string" and useless for "what does a viewer see when one endpoint
is down", which is a property of execution. So this one executes the real
`<script>` under a stub DOM and a stub `fetch`, and asks what ended up visible.

IT FOUND TWO DEFECTS THAT SOURCE-READING COULD NOT, both shipped by me in A05.2
and both invisible on a healthy box:

1. `Promise.all` made every endpoint a single point of failure. A 503 from ONE
   route rejected the whole chain, the `catch` hid every section, and the page
   read `HTTP 503` with nothing on it — a dead platform rather than one unwired
   subsystem. Measured before the fix: `visible sections: (none)`.
   It also made the handlers' `wired: false` payload UNREACHABLE. `_handle_config`
   and `_handle_skills` return 503 with `{"wired": false}` precisely so the page
   can tell "you have configured nothing" from "I cannot look", and `get()` threw
   on any non-ok status, so the renderers' `if (!payload.wired)` branches could
   never run. A write with no reader, one protocol further out.

2. "Forget token" hid `health` and `schedules` and nothing else. After A05.2 the
   SETTINGS table stayed on screen — every configured key, still rendered, after
   the operator asked the page to forget. Measured before the fix:
   `visible after forget: config,skills`.

THE CAUSE IS ONE THING, NOT TWO: the page enumerated its own sections BY HAND in
three places — the fetch list, the catch list and the forget list — so adding a
route meant remembering three edits, and A05.2 remembered one. `PANELS` is now
the single enumeration and all three read from it. Same shape as the stale
hand-written tuple deleted from the bijection guard in this change: a list beside
the thing that made lists unnecessary.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from stackowl.control_plane.page import INDEX_HTML

_SECTIONS = ("health", "schedules", "config", "skills", "tasks", "agents")

_HARNESS = r"""
const fs = require("fs");
function mkEl(id) {
  return {
    id, hidden: false, textContent: "", className: "", value: "",
    children: [], _listeners: {},
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(k, f) { this._listeners[k] = f; },
  };
}
const els = {};
global.document = {
  getElementById: (id) => (els[id] = els[id] || mkEl(id)),
  createElement: (t) => mkEl("<" + t + ">"),
};
global.location = { origin: "http://127.0.0.1:8787" };
global.sessionStorage = {
  _s: { "stackowl.control.token": "tok" },
  getItem(k) { return this._s[k] ?? null; },
  setItem(k, v) { this._s[k] = v; },
  removeItem(k) { delete this._s[k]; },
};
// GENERIC BY ROUTE, never a hand-written map. The first version listed the four
// routes that existed when it was written, so A05.6's fifth route made both
// tests fail with `status: undefined` — the stub, not the page. That is the same
// hand-written-list defect this file's own `test_the_panel_list_is_written_ONCE`
// exists to prevent, reproduced inside the harness that proves it. Every path
// answers 200 with an empty payload of every shape the renderers read; only the
// route under test carries the status being probed.
const SKILLS_STATUS = Number(process.argv[3]);
const PROBED = "/api/v1/skills";
const EMPTY = { subsystems: [], schedules: [], settings: [], owls: [], tasks: [],
                wired: true };
global.fetch = (path) => {
  const status = path === PROBED ? SKILLS_STATUS : 200;
  const body = path === PROBED && status !== 200
    ? Object.assign({}, EMPTY, { wired: false }) : EMPTY;
  return Promise.resolve({
    status, ok: status >= 200 && status < 300,
    json: () => Promise.resolve(body),
  });
};
eval(fs.readFileSync(process.argv[2], "utf8"));
// SECOND hand-written list in this harness, and it hid behind the first: with
// the fetch stub fixed, the page rendered `tasks` correctly and the harness
// still reported four sections, because it only looked at four. Passed in from
// the test, which derives it from `_SECTIONS`.
const NAMES = JSON.parse(process.argv[4]);
const visible = () => NAMES.filter((id) => els[id] && els[id].hidden === false);
setTimeout(() => {
  const after_load = visible();
  els.forget._listeners.click();
  console.log(JSON.stringify({
    after_load, status: els.status.textContent, after_forget: visible(),
  }));
}, 50);
"""


def _run(tmp_path: Path, skills_status: int) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:  # pragma: no cover — present on this box and in CI
        pytest.skip("no node on PATH; the page cannot be executed here")

    match = re.search(r"<script>(.*?)</script>", INDEX_HTML, re.S)
    assert match, "the page has no <script> block — this guard has gone blind"
    script = tmp_path / "page.js"
    script.write_text(match.group(1), encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(textwrap.dedent(_HARNESS), encoding="utf-8")

    proc = subprocess.run(
        [node, str(harness), str(script), str(skills_status), json.dumps(list(_SECTIONS))],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"the page threw: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.tripwire
def test_a_healthy_platform_renders_every_section(tmp_path: Path) -> None:
    """The vacuity control. Without it the 503 test below could pass because the
    page renders nothing under this harness at all."""
    out = _run(tmp_path, 200)

    assert out["after_load"] == list(_SECTIONS), (
        f"a fully healthy platform did not render every section: {out}"
    )


@pytest.mark.tripwire
def test_ONE_unwired_route_does_not_blank_the_whole_page(tmp_path: Path) -> None:
    """`/api/v1/skills` answers 503 `wired: false` when no db is injected. That
    is a designed answer about ONE subsystem, and it used to blank all four
    tables."""
    out = _run(tmp_path, 503)

    assert out["after_load"] == list(_SECTIONS), (
        "a 503 from one route hid other sections — the page reports a dead "
        f"platform when one subsystem is unwired: {out}"
    )
    assert "503" not in str(out["status"]), (
        f"the status line surfaced a transport error for a designed answer: {out}"
    )


@pytest.mark.tripwire
def test_forgetting_the_token_hides_EVERY_section(tmp_path: Path) -> None:
    """The operator asked the page to forget. Leaving the settings table on a
    shared screen is the disclosure DEBT-309 spent a whole item preventing on
    the wire."""
    out = _run(tmp_path, 200)

    assert out["after_forget"] == [], (
        f"these sections survived 'forget token': {out['after_forget']}"
    )


@pytest.mark.tripwire
def test_the_panel_list_is_written_ONCE() -> None:
    """The cause, pinned. Three hand-written enumerations is how A05.2 updated
    one of them; a fourth would rot the same way."""
    match = re.search(r"<script>(.*?)</script>", INDEX_HTML, re.S)
    assert match
    script = match.group(1)

    declared = re.findall(r'\{\s*id:\s*"(\w+)"', script)
    assert sorted(declared) == sorted(_SECTIONS), (
        f"PANELS no longer declares exactly the rendered sections: {declared}"
    )
    # No section may be HIDDEN by name. Revealing is per-panel and local (each
    # renderer unhides its own); hiding is the sweep that has to cover all of
    # them, and naming them there is exactly the list that went stale twice.
    hidden_by_name = [
        h for h in re.findall(r'\$\("(\w+)"\)\.hidden\s*=\s*true', script)
        if h in _SECTIONS
    ]
    assert not hidden_by_name, (
        f"these sections are hidden by name rather than through PANELS: "
        f"{hidden_by_name} — that is the hand-written list coming back"
    )
