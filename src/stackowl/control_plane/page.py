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
#: The installable-app declaration, and the mark, as CONSTANTS.
#:
#: THEY MUST BE SAME-ORIGIN ROUTES RATHER THAN `data:` URIs — a browser refuses a
#: `data:` manifest, so "no external request" is satisfied by serving them from
#: this door rather than by inlining them. Neither is an `/api/` path, so the
#: route/page bijection (which filters on `/api/`) is untouched.
#:
#: AND NEITHER HANDLER MAY READ INSTANCE STATE. `test_every_route_handler_calls_
#: authenticate` allows an unguarded route only if it CANNOT reach platform state
#: — no instance attribute at all — which is why `start_url` is RELATIVE: putting
#: the configured port in here would make the manifest need `self._settings` and
#: forfeit the exemption. A browser fetches a manifest before anyone signs in, so
#: unguarded is the only workable answer, and relative is what makes it safe.
MANIFEST_JSON: Final = """{
  "name": "StackOwl control plane",
  "short_name": "StackOwl",
  "description": "Mission control for a self-hosted kernel of persistent agents.",
  "display": "standalone",
  "start_url": "/",
  "scope": "/",
  "background_color": "#0b0e12",
  "theme_color": "#0b0e12",
  "icons": [
    { "src": "/icon.svg", "sizes": "any", "type": "image/svg+xml",
      "purpose": "any maskable" }
  ]
}
"""

#: The same path the header renders, on its own ground so a home-screen icon has
#: an opaque tile rather than a transparent one.
ICON_SVG: Final = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<rect width="24" height="24" rx="4" fill="#0b0e12"/>
<path fill="#f4f1ea" fill-rule="evenodd" transform="translate(2.4 2.4) scale(0.8)"
 d="M3.5 2H20.5A1.5 1.5 0 0 1 22 3.5V7.5A1.5 1.5 0 0 1 20.5 9H13.4L12 6.9L10.6 9H3.5
    A1.5 1.5 0 0 1 2 7.5V3.5A1.5 1.5 0 0 1 3.5 2Z
    M9 3.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 1 0 0-3.8Z
    M15 3.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 1 0 0-3.8Z
    M3.5 10.5H20.5A1.5 1.5 0 0 1 22 12V14A1.5 1.5 0 0 1 20.5 15.5H3.5
    A1.5 1.5 0 0 1 2 14V12A1.5 1.5 0 0 1 3.5 10.5Z
    M6.5 17H17.5A1.5 1.5 0 0 1 19 18.5V20.5A1.5 1.5 0 0 1 17.5 22H6.5
    A1.5 1.5 0 0 1 5 20.5V18.5A1.5 1.5 0 0 1 6.5 17Z"/>
</svg>
"""


INDEX_HTML: Final = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f2f4f7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0b0e12" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="StackOwl">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/icon.svg">
<title>StackOwl control plane</title>
<style>
  /* SUBSTRATE — the colour law: chrome is monochrome, saturation is a VERDICT.
     A kernel supervising agents that act unwatched is the one product where an
     invented alarm is a real defect, so the palette is built so the page cannot
     colour something it has not judged. Every generic console does the reverse
     and sprays one brand hue across nav, headers and buttons until saturation
     carries no information at all. */
  :root {
    color-scheme: light dark;
    --bg:#f2f4f7; --panel:#ffffff; --sunk:#e9ecf1; --raised:#ffffff;
    --line:#d9dee6; --line-2:#c2cad6;
    --fg:#0f141a; --fg-dim:#4f5b6b; --fg-mute:#78859a;
    --brand:#1b1f26;
    --ok:#0b6b4e; --warn:#8a5a09; --bad:#b02a20; --idle:#78859a;
    --ok-tint:#e6f6ef; --warn-tint:#fdf2dc; --bad-tint:#fce8ee; --idle-tint:#eceff4;
    --mono:ui-monospace,"SF Mono","Cascadia Mono","Roboto Mono",Menlo,Consolas,monospace;
    --ui:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
    --notch:10px;
    --chamfer:polygon(var(--notch) 0,100% 0,100% calc(100% - var(--notch)),
              calc(100% - var(--notch)) 100%,0 100%,0 var(--notch));
    --dur:200ms; --ease:cubic-bezier(.32,.72,0,1);
  }
  /* Dark is redefined TWICE and never defines a colour for the first time: the
     viewer has three states, and the un-stamped "system" one is the common case. */
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --bg:#0b0e12; --panel:#161c24; --sunk:#10151b; --raised:#1d2530;
    --line:#232c38; --line-2:#313d4c;
    --fg:#dfe6ee; --fg-dim:#97a3b2; --fg-mute:#5f6c7c;
    --brand:#f4f1ea;
    --ok:#2fd39a; --warn:#e8a33d; --bad:#f05a4f; --idle:#5f6c7c;
    --ok-tint:#0f2a20; --warn-tint:#2b2310; --bad-tint:#2e1520; --idle-tint:#1b202b;
  } }
  :root[data-theme="dark"] {
    --bg:#0b0e12; --panel:#161c24; --sunk:#10151b; --raised:#1d2530;
    --line:#232c38; --line-2:#313d4c;
    --fg:#dfe6ee; --fg-dim:#97a3b2; --fg-mute:#5f6c7c;
    --brand:#f4f1ea;
    --ok:#2fd39a; --warn:#e8a33d; --bad:#f05a4f; --idle:#5f6c7c;
    --ok-tint:#0f2a20; --warn-tint:#2b2310; --bad-tint:#2e1520; --idle-tint:#1b202b;
  }

  *,*::before,*::after { box-sizing:border-box; }
  html { -webkit-text-size-adjust:100%; -webkit-tap-highlight-color:transparent; }
  body {
    margin:0; min-height:100dvh; font:400 15px/1.5 var(--ui);
    color:var(--fg); background:var(--bg);
    /* Anodize. Pure CSS, no image, invisible until you look for it. */
    background-image:repeating-linear-gradient(180deg,
      rgba(127,127,127,.022) 0 1px, transparent 1px 3px);
    padding-bottom:env(safe-area-inset-bottom);
  }
  /* EVERY numeral is tabular. The cheapest single change that makes a console
     read as engineered rather than typed. */
  body { font-variant-numeric:tabular-nums; }

  header {
    position:sticky; top:0; z-index:5;
    display:flex; align-items:center; gap:12px; flex-wrap:wrap;
    padding:calc(env(safe-area-inset-top) + 14px) 20px 14px;
    background:var(--panel); border-bottom:1px solid var(--line-2);
  }
  .mark { width:26px; height:26px; flex:none; color:var(--brand); }
  .wordmark { font:600 15px/1 var(--mono); letter-spacing:.14em; }
  .wordmark b { color:var(--brand); font-weight:600; }
  .wordmark span { color:var(--fg-mute); }
  .kicker { font:600 10px/1 var(--mono); letter-spacing:.24em;
            color:var(--fg-mute); text-transform:uppercase; margin-top:5px; }
  .brandblock { display:flex; flex-direction:column; }
  .origin { margin-left:auto; font:500 12px/1 var(--mono); color:var(--fg-dim);
            background:var(--sunk); border:1px solid var(--line);
            border-radius:999px; padding:6px 10px; }

  main { padding:20px; display:flex; flex-direction:column; gap:20px;
         max-width:1180px; margin:0 auto; }

  /* A panel is CHAMFERED, not rounded. One 45-degree cut, asymmetric, machined —
     and nothing like the uniform 12px radius with an accent bar that every
     generated dashboard ships. The border rides a 1px parent because a border
     cannot follow clip-path. */
  section {
    position:relative; background:var(--panel); clip-path:var(--chamfer);
    padding:18px 20px 20px;
    /* An INSET shadow, not a border: a border cannot follow `clip-path`, and the
       usual cure is a 1px parent wrapping every panel in a second div. An inset
       shadow is painted INSIDE the element, so the clip trims it and the edge
       follows the chamfer exactly — nine sections keep their markup unchanged. */
    box-shadow:inset 0 0 0 1px var(--line);
  }
  @supports not (clip-path: polygon(0 0)) {
    section { border:1px solid var(--line); border-radius:2px; box-shadow:none; }
  }
  h2 { font:600 13px/1 var(--mono); letter-spacing:.12em; text-transform:uppercase;
       color:var(--fg-dim); margin:0 0 14px; display:flex; align-items:center; gap:10px; }
  h2::before { content:""; width:14px; height:1px; background:var(--line-2); flex:none; }

  .wrap { overflow-x:auto; -webkit-overflow-scrolling:touch;
          overscroll-behavior-x:contain; }
  table { border-collapse:collapse; width:100%; font-size:13px; }
  th { font:600 11px/1.3 var(--mono); letter-spacing:.06em; text-transform:uppercase;
       color:var(--fg-mute); text-align:left; padding:0 14px 8px 0;
       border-bottom:1px solid var(--line-2); white-space:nowrap; }
  td { padding:9px 14px 9px 0; border-top:1px solid var(--line);
       vertical-align:top; max-width:38ch; overflow-wrap:anywhere; }
  tbody tr:hover td { background:var(--sunk); }
  /* Identifiers are machine words and read as mono; prose never does. */
  td:first-child { font:500 13px/1.45 var(--mono); color:var(--fg); white-space:nowrap; }
  .mut { color:var(--fg-mute); }
  .ok { color:var(--ok); } .warn { color:var(--warn); } .bad { color:var(--bad); }
  code { font:500 12px/1.4 var(--mono); background:var(--sunk);
         border:1px solid var(--line); border-radius:4px; padding:1px 5px; }

  .note { margin:14px 0 0; padding-left:12px; border-left:2px solid var(--line-2);
          font-size:12.5px; color:var(--fg-dim); max-width:78ch; }

  form { display:flex; flex-direction:column; gap:12px; max-width:360px; }
  input {
    /* 16px is a FLOOR, not a preference: anything smaller makes iOS Safari zoom
       on focus, and that zoom is the loudest possible "this is a web page". */
    font:400 16px/1.2 var(--ui); height:48px; padding:0 14px;
    color:var(--fg); background:var(--sunk);
    border:1px solid var(--line); border-radius:10px;
  }
  input:focus-visible { outline:2px solid var(--fg-dim); outline-offset:2px;
                        border-color:var(--fg-dim); }
  button {
    font:600 15px/1 var(--ui); height:48px; padding:0 18px; cursor:pointer;
    color:var(--bg); background:var(--fg); border:1px solid var(--fg);
    border-radius:10px; touch-action:manipulation;
  }
  button#forget { color:var(--fg-dim); background:transparent; border-color:var(--line-2); }
  button:active { transform:translateY(1px); }
  button:focus-visible { outline:2px solid var(--fg-dim); outline-offset:2px; }

  #status { padding:0 20px 24px; font:500 12px/1.6 var(--mono); color:var(--fg-mute);
            max-width:1180px; margin:0 auto; }

  /* THE SEAM. One pulse along a panel's top edge when ITS payload lands — eight
     independent fetches, eight arrivals, staggered by real network timing and
     never repeating. An animation that is telling the truth about the
     architecture rather than decorating it. */
  section::before {
    content:""; position:absolute; inset:0 0 auto 0; height:1px; opacity:0;
    background:linear-gradient(90deg,transparent 0,var(--fg-dim) 38%,transparent 72%);
    transform:translateX(-100%); pointer-events:none;
  }
  section:not([hidden])::before { animation:seam .62s var(--ease) both; }
  @keyframes seam {
    0% { transform:translateX(-100%); opacity:0 }
    14% { opacity:1 }
    100% { transform:translateX(100%); opacity:0 }
  }

  @media (max-width:640px) {
    :root { --notch:6px; }
    main { padding:14px; gap:14px; }
    section { padding:14px 14px 16px; }
    header { padding:calc(env(safe-area-inset-top) + 12px) 14px 12px; }
    .origin { margin-left:0; order:3; width:100%; text-align:center; }
    td { max-width:26ch; }
    #status { padding:0 14px 20px; }
  }

  @media (prefers-reduced-motion: reduce) {
    *,*::before,*::after { animation-duration:.01ms !important;
                           transition-duration:.01ms !important; }
    section:not([hidden])::before { animation:none; transform:none;
                                    opacity:.5; background:var(--line-2); }
  }
</style>
</head>
<body>
<header>
  <!-- THE MARK. Three stacked plates; the top one has two punched holes and a
       notch bitten out of its lower edge. At 26px your eye snaps the holes and
       the notch into a face; at a glance it is a stack of layers. It changes
       meaning with scale, which is the product: many agents, one watching thing.
       One path, currentColor, no gradient — it works as a favicon and a nav
       glyph, and it never turns red, because a logo that changes colour is
       asserting a verdict. -->
  <svg class="mark" viewBox="0 0 24 24" fill="currentColor" fill-rule="evenodd"
       aria-hidden="true">
    <path d="M3.5 2H20.5A1.5 1.5 0 0 1 22 3.5V7.5A1.5 1.5 0 0 1 20.5 9H13.4L12 6.9
             L10.6 9H3.5A1.5 1.5 0 0 1 2 7.5V3.5A1.5 1.5 0 0 1 3.5 2Z
             M9 3.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 1 0 0-3.8Z
             M15 3.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 1 0 0-3.8Z
             M3.5 10.5H20.5A1.5 1.5 0 0 1 22 12V14A1.5 1.5 0 0 1 20.5 15.5H3.5
             A1.5 1.5 0 0 1 2 14V12A1.5 1.5 0 0 1 3.5 10.5Z
             M6.5 17H17.5A1.5 1.5 0 0 1 19 18.5V20.5A1.5 1.5 0 0 1 17.5 22H6.5
             A1.5 1.5 0 0 1 5 20.5V18.5A1.5 1.5 0 0 1 6.5 17Z"/>
  </svg>
  <span class="brandblock">
    <span class="wordmark"><span>STACK</span><b>OWL</b></span>
    <span class="kicker">Control plane</span>
  </span>
  <span class="origin" id="where"></span>
</header>
<main>
  <section id="gate">
    <h2>Sign in</h2>
    <form id="loginform">
      <input id="username" type="text" placeholder="username"
             autocomplete="username" spellcheck="false">
      <input id="password" type="password" placeholder="password"
             autocomplete="current-password" spellcheck="false">
      <button type="submit">Sign in</button>
      <button type="button" id="forget">Sign out</button>
    </form>
    <p class="note" id="defaultwarn" hidden></p>
    <p class="note">Set <code>control_plane.username</code> and
       <code>control_plane.password</code> in <code>stackowl.yaml</code>. The
       session is kept for this tab only and never written to a cookie.</p>
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

  <section id="interactions" hidden>
    <h2>How the agents interact</h2>
    <div class="wrap"><table>
      <thead><tr><th>When</th><th>Kind</th><th>From</th><th>To</th><th>Outcome</th><th>What</th></tr></thead>
      <tbody id="edgerows"></tbody>
    </table></div>
    <p class="note" id="edgenote"></p>
  </section>

  <section id="memory" hidden>
    <h2>Memory</h2>
    <div class="wrap"><table>
      <thead><tr><th>About</th><th>Remembered</th><th>Durability</th></tr></thead>
      <tbody id="curatedrows"></tbody>
    </table></div>
    <p class="note">What the platform has been told to remember, from
       <code>~/.stackowl/memory</code> — the same notes <code>/memory search</code>
       reads. <code>user</code> is about you; the rest are per-owl working notes.</p>
    <h2>Learned from doing the work</h2>
    <div class="wrap"><table>
      <thead><tr><th>Kind</th><th>Lesson</th><th>When</th></tr></thead>
      <tbody id="lessonrows"></tbody>
    </table></div>
    <p class="note" id="memorynote"></p>
  </section>

  <section id="agents" hidden>
    <h2>Agents</h2>
    <div class="wrap"><table>
      <thead><tr><th>Agent</th><th>Role</th><th>Tier</th><th>Authority</th>
                 <th>Skills (card / owned)</th><th>In flight</th><th>Turns/3d</th></tr></thead>
      <tbody id="agentrows"></tbody>
    </table></div>
    <p class="note"><strong>Authority</strong> is computed by the same
       <code>effective_bounds</code> fold the enforcement path uses.
       <code>unbounded</code> does not mean "default" — the function's own
       docstring calls it <em>genuinely unbounded</em>, and the gate then reads
       it as unrestricted. An unbounded agent may use every tool on the platform.
       <strong>Skills</strong> shows two numbers because there are two answers:
       what the agent's card declares, and what the ownership table records. They
       disagree today, and merging them would hide that.</p>
  </section>

  <section id="tasks" hidden>
    <h2>Work in flight</h2>
    <div class="wrap"><table>
      <thead><tr><th>Task</th><th>Owl</th><th>Status</th><th>Blocked</th>
                 <th>Attempts</th><th>Next run</th><th>Last error</th></tr></thead>
      <tbody id="taskrows"></tbody>
    </table></div>
    <p class="note">UNFINISHED work only — completed and failed rows are history.
       <strong>Blocked</strong> says why a pending row is not moving:
       <code>terminal_parent</code> means its parent finished, so the loop will
       never run it, and that is correct rather than stuck. <code>none</code>
       means the loop may take it now.</p>
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
    { id: "skills",    path: "/api/v1/skills",    render: renderSkills },
    { id: "tasks",     path: "/api/v1/tasks",     render: renderTasks },
    { id: "agents",    path: "/api/v1/agents",    render: renderAgents },
    { id: "memory",    path: "/api/v1/memory",    render: renderMemory },
    { id: "interactions", path: "/api/v1/interactions", render: renderInteractions }
  ];

  function renderInteractions(payload) {
    var body = $("edgerows");
    body.textContent = "";
    if (!payload.wired) {
      var r = document.createElement("tr");
      cell(r, "no db wired", "mut");
      body.appendChild(r);
      $("interactions").hidden = false;
      return;
    }
    (payload.edges || []).forEach(function (e) {
      var row = document.createElement("tr");
      cell(row, (e.at || "").slice(0, 19), "mut");
      // The two kinds are LABELLED, never summed. 52 of 52 task edges are an owl
      // decomposing its own work, and showing that as "interactions" would
      // report a busy multi-agent platform that is one owl talking to itself.
      cell(row, e.kind, e.kind === "delegation" ? "" : "mut");
      cell(row, e.from_owl === null ? "—" : e.from_owl, e.from_owl === null ? "mut" : "");
      cell(row, e.to_owl === null ? "—" : e.to_owl, e.to_owl === null ? "mut" : "");
      cell(row, e.outcome, e.outcome === "ok" || e.outcome === "completed" ? "" : "mut");
      cell(row, e.detail || "");
      body.appendChild(row);
    });
    // THE DENOMINATOR IS THE POINT, again. An operator reading "2 delegations"
    // would conclude the agents barely talk; the honest line says how many of
    // the recorded edges are missing an end, and when the newest one was.
    var g = payload.gaps || {};
    var d = (payload.edges || []).filter(function (e) { return e.kind === "delegation"; }).length;
    var parts = [
      d + " delegation / " + ((payload.edges || []).length - d) + " decomposition edges",
      "newest delegation recorded: " + (g.newest_delegation_recorded || "none").slice(0, 19),
      g.delegations_without_a_caller + " with no recoverable caller (the calling task row is gone)",
      g.delegations_without_a_target + " with no recorded target",
      "parliament sessions: " + g.parliament_sessions
    ];
    if (g.truncated) { parts.push("TRUNCATED — more edges than this page shows"); }
    if (g.unseen_other_owner) { parts.push(g.unseen_other_owner + " edges belong to another owner"); }
    $("edgenote").textContent = parts.join(" · ");
    $("interactions").hidden = false;
  }

  function renderMemory(payload) {
    var cur = $("curatedrows");
    var les = $("lessonrows");
    cur.textContent = "";
    les.textContent = "";
    if (!payload.wired) {
      var r = document.createElement("tr");
      cell(r, "no db wired", "mut");
      cur.appendChild(r);
      $("memory").hidden = false;
      return;
    }
    (payload.curated || []).forEach(function (t) {
      (t.entries || []).forEach(function (e) {
        var row = document.createElement("tr");
        cell(row, t.target, t.target === "user" ? "" : "mut");
        cell(row, e.text);
        cell(row, e.durability, "mut");
        cur.appendChild(row);
      });
    });
    (payload.lessons || []).forEach(function (l) {
      var row = document.createElement("tr");
      cell(row, l.source_type, "mut");
      cell(row, l.content);
      cell(row, l.created_at, "mut");
      les.appendChild(row);
    });
    // THE DENOMINATOR IS THE POINT. The lessons table is a WINDOW onto 5,964
    // rows; without the totals beside it a reader takes fifty for all of them.
    // And each other store says what KIND it is, because `staged_facts` is
    // short-term conversation history and rendering it as "memories" would
    // present a transcript as knowledge.
    var parts = [];
    var total = payload.total || 0;
    parts.push("showing " + (payload.lessons || []).length + " of " + total +
               " lessons (" + JSON.stringify(payload.counts_by_source || {}) + ")");
    (payload.other_stores || []).forEach(function (st) {
      parts.push(st.store + ": " + (st.rows === null ? "unreadable" : st.rows) +
                 " — " + st.kind);
    });
    $("memorynote").textContent = parts.join(" · ");
    $("memory").hidden = false;
  }

  function renderAgents(payload) {
    var body = $("agentrows");
    body.textContent = "";
    if (!payload.wired) {
      var r = document.createElement("tr");
      cell(r, "no db wired", "mut");
      body.appendChild(r);
    } else {
      (payload.agents || []).forEach(function (a) {
        var row = document.createElement("tr");
        cell(row, a.display_name);
        cell(row, a.role, "mut");
        cell(row, a.model_tier, "mut");
        // A PILL HERE IS CORRECT, unlike on `blocked`: this IS a verdict about
        // authority, and "unbounded" is the one an operator must not skim past.
        if (a.unbounded) {
          pill(row, "unbounded", "bad");
        } else {
          pill(row, boundSummary(a.bounds), "ok");
        }
        cell(row, a.skills_on_card + " / " + a.skills_owned,
             a.skills_on_card === a.skills_owned ? "mut" : "warn");
        cell(row, a.in_flight, "num");
        cell(row, a.recent_turns, "num");
        body.appendChild(row);
      });
    }
    $("agents").hidden = false;
  }

  function boundSummary(bounds) {
    if (!bounds) { return "bounded"; }
    var tools = bounds.tools;
    return tools ? tools.length + " tools" : "bounded";
  }

  function blockedKind(blocked) {
    if (blocked === "none") { return "ok"; }
    if (blocked === "terminal_parent" || blocked === "superseded") { return "mut"; }
    return "warn";
  }

  function renderTasks(payload) {
    var body = $("taskrows");
    body.textContent = "";
    if (!payload.wired) {
      var r = document.createElement("tr");
      cell(r, "no db wired", "mut");
      body.appendChild(r);
    } else if (!(payload.tasks || []).length) {
      var e = document.createElement("tr");
      cell(e, "no unfinished work", "mut");
      body.appendChild(e);
    } else {
      (payload.tasks || []).forEach(function (t) {
        var row = document.createElement("tr");
        cell(row, t.task_id);
        cell(row, t.owl_name, "mut");
        cell(row, t.status);
        // NOT a pill: `blocked` is not a health verdict. `terminal_parent` is
        // the loop behaving correctly, and colouring it like a fault is the
        // invented alarm this whole column exists to prevent.
        cell(row, t.blocked, blockedKind(t.blocked));
        cell(row, t.attempt_count + "/" + t.max_attempts, "num");
        cell(row, t.next_attempt_at, "mut");
        cell(row, t.last_error, "mut");
        body.appendChild(row);
      });
    }
    $("tasks").hidden = false;
  }

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

  // The password NEVER goes to sessionStorage — only the token the server hands
  // back does. A page that remembered the password would put a reusable
  // credential where any script on this origin can read it, to save one typing.
  $("loginform").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var u = $("username").value;
    var p = $("password").value;
    if (!u || !p) { say("enter a username and password"); return; }
    say("signing in…");
    fetch("/api/v1/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: p })
    }).then(function (r) {
      if (r.status === 401) { throw new Error("invalid credentials"); }
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    }).then(function (body) {
      $("password").value = "";
      if (body.default_credentials) {
        var w = $("defaultwarn");
        w.textContent = "You are signed in with the DEFAULT credentials " +
          "(admin/admin). Anyone who can reach this address can sign in. " +
          "Set control_plane.username and control_plane.password.";
        w.className = "note bad";
        w.hidden = false;
      }
      try { sessionStorage.setItem(KEY, body.token); } catch (e) { /* memory only */ }
      load(body.token);
    }).catch(function (e) {
      say(String(e.message || e));
    });
  });

  $("forget").addEventListener("click", function () {
    try { sessionStorage.removeItem(KEY); } catch (e) { /* nothing to clear */ }
    $("username").value = "";
    $("password").value = "";
    $("defaultwarn").hidden = true;
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

  // THE RESUME PATH, AND IT WAS DEAD. This read `$("token").value = stored`
  // before calling `load` — and DEBT-310 replaced the token input with the
  // username/password form, so `id="token"` has appeared ZERO times in the
  // markup since. `$()` returned null, `.value =` threw a TypeError, the IIFE
  // died, and `load(stored)` never ran: a returning tab that already held a
  // session rendered NOTHING and said nothing about why. Fresh sign-in survived
  // because that path calls `load(body.token)` directly, which is exactly why it
  // went unnoticed — the half that broke is the half nobody exercises twice.
  var stored = null;
  try { stored = sessionStorage.getItem(KEY); } catch (e) { stored = null; }
  if (stored) { load(stored); }
  else { say("sign in to read the platform"); }
})();
</script>
</body>
</html>
"""
