"""The one page. Static markup, zero platform data, served without a credential.

WHY THIS EXISTS, AND WHY IT IS SEPARATE FROM EVERY OTHER ROUTE.

A05.1's gap read "there is no surface through which a customer can see or steer
the platform. Everything is a terminal command." The item shipped and was marked
complete, and what it shipped was a JSON API behind a bearer token — which is
still a terminal command, typed into `curl` instead of into `stackowl`. The
operator asked, in his own words, "what happened with agentic os dashboard?",
and the honest answer was that nothing rendered. `map_check.py` agreed: eleven
items matched "a web page a customer opens in a browser" and not one of them
BUILDS a page. Every A05 item says *make X reachable*; none says *draw it*.

THE PAGE CARRIES NO DATA AND THAT IS THE WHOLE SECURITY ARGUMENT.

A browser cannot put an `Authorization` header on a top-level navigation, so a
page served only to an authenticated caller could never be opened. This route is
therefore the ONE route with no credential check — and it is safe for exactly one
reason, which is enforced by a test rather than promised here: this module is a
string constant with no interpolation, so the page cannot leak platform state
even by accident. Every byte of data on the rendered dashboard arrives from
`fetch()` calls the browser makes AFTER the viewer supplies the token, and each
of those goes through `_guard` like everything else.

The token is held in `sessionStorage`, not a cookie: a cookie would be sent
automatically on every request to this origin, which is how a dashboard acquires
a CSRF problem it did not need.

NO CDN, NO FRAMEWORK, NO BUILD STEP. The platform is self-hosted by
requirement; a page that fetches a framework from someone else's server does not
work on a machine with no egress, and this one has to work on the operator's own
box first.
"""

from __future__ import annotations

from typing import Final

#: The entire page. A CONSTANT, never an f-string — see the module docstring and
#: `test_the_page_cannot_carry_platform_data`.
INDEX_HTML: Final = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>StackOwl control plane</title>
<style>
  :root { color-scheme: light dark; --bg:#fbfaf8; --fg:#1b1a18; --mut:#6b665f;
          --line:#e2ddd5; --card:#ffffff; --ok:#2f6f4f; --warn:#8a6d1f; --bad:#9c3128; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16151a; --fg:#eceaf0; --mut:#9d98a6; --line:#2e2b36; --card:#1e1d24;
            --ok:#6fcf97; --warn:#e3c268; --bad:#e0887e; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 ui-sans-serif,
         system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  header { padding:22px 24px 14px; border-bottom:1px solid var(--line);
           display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }
  h1 { font-size:17px; margin:0; letter-spacing:-.01em; }
  .sub { color:var(--mut); font-size:13px; }
  main { padding:22px 24px 48px; max-width:1100px; }
  section { margin-bottom:34px; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.08em;
       color:var(--mut); margin:0 0 12px; font-weight:600; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th { text-align:left; font-weight:600; color:var(--mut); font-size:12px;
       text-transform:uppercase; letter-spacing:.05em; padding:0 12px 8px 0; }
  td { padding:9px 12px 9px 0; border-top:1px solid var(--line);
       vertical-align:top; }
  td.num { font-variant-numeric: tabular-nums; }
  .wrap { overflow-x:auto; }
  .pill { display:inline-block; padding:1px 8px; border-radius:999px;
          font-size:12px; border:1px solid var(--line); }
  .ok   { color:var(--ok);   border-color:var(--ok); }
  .warn { color:var(--warn); border-color:var(--warn); }
  .bad  { color:var(--bad);  border-color:var(--bad); }
  .mut  { color:var(--mut); }
  form { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  input { font:inherit; padding:7px 10px; border:1px solid var(--line);
          border-radius:7px; background:var(--card); color:var(--fg); min-width:340px; }
  button { font:inherit; padding:7px 14px; border:1px solid var(--line);
           border-radius:7px; background:var(--card); color:var(--fg); cursor:pointer; }
  button:hover { border-color:var(--mut); }
  code { font:13px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
  .note { color:var(--mut); font-size:13px; margin:10px 0 0; }
</style>
</head>
<body>
<header>
  <h1>StackOwl control plane</h1>
  <span class="sub" id="where"></span>
</header>
<main>
  <section id="gate">
    <h2>Credential</h2>
    <form id="tokenform">
      <input id="token" type="password" placeholder="control-plane token"
             autocomplete="off" spellcheck="false">
      <button type="submit">Open</button>
      <button type="button" id="forget">Forget</button>
    </form>
    <p class="note">Run <code>stackowl control-plane</code> on the host to print it.
       It is kept for this tab only and never written to a cookie.</p>
  </section>

  <section id="health" hidden>
    <h2>Subsystems</h2>
    <div class="wrap"><table>
      <thead><tr><th>Subsystem</th><th>Status</th><th>Message</th>
                 <th>Remedy</th><th>Latency</th></tr></thead>
      <tbody id="healthrows"></tbody>
    </table></div>
  </section>

  <section id="schedules" hidden>
    <h2>Schedules</h2>
    <div class="wrap"><table>
      <thead><tr><th>Job</th><th>Handler</th><th>Schedule</th><th>State</th>
                 <th>Next run</th><th>Failures</th><th>Last error</th></tr></thead>
      <tbody id="schedulerows"></tbody>
    </table></div>
  </section>

  <section id="config" hidden>
    <h2>Settings</h2>
    <div class="wrap"><table>
      <thead><tr><th>Key</th><th>Value</th></tr></thead>
      <tbody id="configrows"></tbody>
    </table></div>
    <p class="note">What is CONFIGURED, not what is effective — a default nobody
       set does not appear here, exactly as <code>/config list</code> behaves.
       Credentials read <code>***</code>.</p>
  </section>

  <section id="skills" hidden>
    <h2>Skill ownership</h2>
    <div class="wrap"><table>
      <thead><tr><th>Owl</th><th>Skills</th><th>Owned</th></tr></thead>
      <tbody id="skillrows"></tbody>
    </table></div>
    <p class="note">Which owl owns which skill. An owl with none does not appear —
       ownership is recorded per pair, not per owl.</p>
  </section>

  <p class="note" id="status"></p>
</main>
<script>
(function () {
  var KEY = "stackowl.control.token";
  var $ = function (id) { return document.getElementById(id); };
  $("where").textContent = location.origin;

  function say(msg) { $("status").textContent = msg; }

  // textContent everywhere, never innerHTML: `last_error` is a string the
  // platform stores from a failing job, so it is untrusted markup by the time
  // it reaches here.
  function cell(row, text, cls) {
    var td = document.createElement("td");
    td.textContent = text === null || text === undefined || text === "" ? "—" : String(text);
    if (cls) { td.className = cls; }
    row.appendChild(td);
    return td;
  }

  function pill(row, text, kind) {
    var td = document.createElement("td");
    var span = document.createElement("span");
    span.className = "pill " + kind;
    span.textContent = text;
    td.appendChild(span);
    row.appendChild(td);
  }

  function get(path, token) {
    return fetch(path, { headers: { "Authorization": "Bearer " + token } })
      .then(function (r) {
        if (r.status === 401 || r.status === 403) { throw new Error("refused (" + r.status + ")"); }
        // 503 + `wired: false` is the platform CONFESSING it cannot look, which
        // is a different answer from "you have nothing" and is the whole reason
        // the handlers return it. Throwing here made that payload unreachable by
        // any browser: the renderers' `if (!payload.wired)` branches could never
        // run. A write with no reader, one protocol out.
        if (r.status === 503) { return r.json(); }
        if (!r.ok) { throw new Error("HTTP " + r.status); }
        return r.json();
      });
  }

  function healthKind(status) {
    if (status === "healthy" || status === "ok") { return "ok"; }
    if (status === "down" || status === "unhealthy" || status === "failed") { return "bad"; }
    return "warn";
  }

  function renderHealth(payload) {
    var body = $("healthrows");
    body.textContent = "";
    if (!payload.wired) {
      var r = document.createElement("tr");
      cell(r, "no health aggregator wired", "mut");
      body.appendChild(r);
    } else {
      (payload.subsystems || []).forEach(function (s) {
        var r = document.createElement("tr");
        cell(r, s.name);
        pill(r, s.status, healthKind(s.status));
        cell(r, s.message, "mut");
        cell(r, s.remedy, "mut");
        cell(r, s.latency_ms === null ? "—" : Math.round(s.latency_ms) + " ms", "num mut");
        body.appendChild(r);
      });
    }
    $("health").hidden = false;
  }

  function renderSchedules(payload) {
    var body = $("schedulerows");
    body.textContent = "";
    if (!payload.wired) {
      var r = document.createElement("tr");
      cell(r, "no scheduler wired", "mut");
      body.appendChild(r);
    } else {
      (payload.schedules || []).forEach(function (j) {
        var row = document.createElement("tr");
        cell(row, j.job_id);
        cell(row, j.handler, "mut");
        cell(row, j.schedule);
        pill(row, j.enabled ? j.status : "disabled", j.enabled ? "ok" : "mut");
        cell(row, j.next_run_at, "num mut");
        cell(row, j.failure_count, j.failure_count > 0 ? "num bad" : "num mut");
        cell(row, j.last_error, "mut");
        body.appendChild(row);
      });
    }
    $("schedules").hidden = false;
  }

  function renderConfig(payload) {
    var body = $("configrows");
    body.textContent = "";
    if (!payload.wired) {
      var r = document.createElement("tr");
      cell(r, "no config file on disk", "mut");
      body.appendChild(r);
    } else {
      (payload.settings || []).forEach(function (s) {
        var row = document.createElement("tr");
        cell(row, s.key);
        cell(row, s.value, s.masked ? "mut" : "");
        body.appendChild(row);
      });
    }
    $("config").hidden = false;
  }

  function renderSkills(payload) {
    var body = $("skillrows");
    body.textContent = "";
    if (!payload.wired) {
      var r = document.createElement("tr");
      cell(r, "no db wired", "mut");
      body.appendChild(r);
    } else {
      (payload.owls || []).forEach(function (o) {
        var row = document.createElement("tr");
        cell(row, o.owl);
        cell(row, (o.skills || []).join(", "), "mut");
        cell(row, o.count, "num");
        body.appendChild(row);
      });
    }
    $("skills").hidden = false;
  }

  // ONE ROUTE'S OUTCOME IS NOT THE PAGE'S OUTCOME. `Promise.all` made every
  // endpoint a single point of failure for the whole dashboard: one 503 and all
  // four tables vanished behind `HTTP 503`, which reads as a dead platform
  // rather than as one unwired subsystem. It gets strictly worse as A05.5/6/7
  // add routes, so the shape is fixed here rather than the instance.
  var PANELS = [
    { id: "health",    path: "/api/v1/health",    render: renderHealth },
    { id: "schedules", path: "/api/v1/schedules", render: renderSchedules },
    { id: "config",    path: "/api/v1/config",    render: renderConfig },
    { id: "skills",    path: "/api/v1/skills",    render: renderSkills }
  ];

  function load(token) {
    say("loading…");
    Promise.allSettled(PANELS.map(function (p) { return get(p.path, token); }))
      .then(function (results) {
        var failed = [];
        results.forEach(function (res, i) {
          var panel = PANELS[i];
          if (res.status === "fulfilled") {
            panel.render(res.value);
          } else {
            $(panel.id).hidden = true;
            failed.push(panel.id + ": " + String(res.reason && res.reason.message
                                                 || res.reason));
          }
        });
        var ok = results.filter(function (r) { return r.status === "fulfilled"; });
        if (!ok.length) { say(failed.join(" · ")); return; }
        var sched = results[1].status === "fulfilled"
          ? (results[1].value.schedules || []).length : null;
        var cfg = results[2].status === "fulfilled"
          ? (results[2].value.settings || []).length : null;
        var parts = [];
        if (sched !== null) { parts.push(sched + " schedule" + (sched === 1 ? "" : "s")); }
        if (cfg !== null) { parts.push(cfg + " setting" + (cfg === 1 ? "" : "s")); }
        parts.push("read at " + new Date().toLocaleTimeString());
        say(parts.concat(failed).join(" · "));
      });
  }

  $("tokenform").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var t = $("token").value.trim();
    if (!t) { say("no token entered"); return; }
    try { sessionStorage.setItem(KEY, t); } catch (e) { /* private mode: memory only */ }
    load(t);
  });

  $("forget").addEventListener("click", function () {
    try { sessionStorage.removeItem(KEY); } catch (e) { /* nothing to clear */ }
    $("token").value = "";
    // FROM `PANELS`, never a list written here. This handler hid `health` and
    // `schedules` and nothing else, so after A05.2 "forget the token" left the
    // SETTINGS table on screen — every key the operator has configured, still
    // rendered, on a shared or walked-past display. A05.8 would have added a
    // fourth. The page enumerated its own sections by hand in three places and
    // A05.2 remembered one of them, which is why the enumeration is gone rather
    // than corrected.
    PANELS.forEach(function (p) { $(p.id).hidden = true; });
    say("token forgotten");
  });

  var stored = null;
  try { stored = sessionStorage.getItem(KEY); } catch (e) { stored = null; }
  if (stored) { $("token").value = stored; load(stored); }
  else { say("enter the token to read the platform"); }
})();
</script>
</body>
</html>
"""
