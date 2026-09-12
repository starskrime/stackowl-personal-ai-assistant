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
<html lang="en" data-scheme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="#06080B" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#06080B" media="(prefers-color-scheme: light)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="StackOwl">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="apple-touch-icon" href="/icon.svg">
<title>StackOwl control plane</title>
<style>
  /* ------------------------------------------------------------------ tokens
     SEVEN VALUES, AND THE RULE THAT GOVERNS THEM: no pixel may imply a
     measurement it cannot cite. Saturation is evidence, never emphasis — on a
     kernel whose agents act unwatched, an invented alarm is a real defect and
     an invented REASSURANCE is the worse one. So `ok` carries no colour at all:
     a green field trains the eye to scan for the exception, and then the
     exception is invisible. */
  :root {
    --void:#06080B;       /* the only background. Panels have no fill. */
    --rule:#1C232C;       /* every hairline, tick and hatch */
    --ink:#D2DAE2;        /* all primary text, and `ok` */
    --ink-dim:#69778A;    /* labels, units, captions, and `unknown` */
    --live:#63E0C8;       /* evidence that a measurement just landed. Nothing else. */
    --degraded:#D69A3C;   /* measured partial failure */
    --down:#CC5240;       /* measured total failure */

    --mono:ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    --sans:system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
    --ease:cubic-bezier(.2,.7,.3,1);
    --bar:56px;
  }
  /* A DELIBERATE SINGLE VISUAL WORLD. This is an instrument for a dark room and
     a phone at night; a light variant would be a second design to keep honest,
     not a courtesy. The ground is painted explicitly so the page never borrows
     a host's, and the media query below exists so the declaration is a CHOICE
     rather than an omission. */
  @media (prefers-color-scheme: light) { :root { --void:#06080B; } }

  * { box-sizing:border-box; }
  html, body { height:100%; }
  body {
    margin:0; background:var(--void); color:var(--ink);
    font:400 13px/1.45 var(--mono);
    font-variant-numeric:tabular-nums;
    -webkit-font-smoothing:antialiased;
    overflow:hidden;                 /* only the list region scrolls */
  }

  /* ------------------------------------------------------------------ shell */
  #app { display:flex; flex-direction:column; height:100dvh; }

  #top {
    flex:none; display:flex; align-items:center; gap:10px;
    padding:calc(env(safe-area-inset-top) + 10px) 14px 10px;
    border-bottom:1px solid var(--rule);
  }
  .mark { width:22px; height:22px; flex:none; color:var(--ink); }
  .wordmark { font:500 12px/1 var(--mono); letter-spacing:.2em; color:var(--ink); }
  .wordmark b { font-weight:500; color:var(--ink-dim); }

  /* THE FRESHNESS READOUT. The one element allowed --live, and it earns it by
     printing the timestamp it is derived from right beside itself — which is
     the rule, stated as markup. */
  #fresh { margin-left:auto; display:flex; align-items:center; gap:8px;
           font:500 10px/1 var(--mono); letter-spacing:.18em;
           text-transform:uppercase; color:var(--ink-dim); }
  #freshbar { width:38px; height:2px; background:var(--rule); position:relative; overflow:hidden; }
  #freshbar i { position:absolute; inset:0; background:var(--live); transform:translateX(-100%); }
  #freshbar.tick i { animation:sweep .6s var(--ease) 1; }
  @keyframes sweep { from { transform:translateX(-100%) } to { transform:translateX(100%) } }

  main { flex:1 1 auto; overflow-y:auto; -webkit-overflow-scrolling:touch;
         padding:12px 14px calc(env(safe-area-inset-bottom) + var(--bar) + 18px); }

  /* ------------------------------------------------------------------- rail */
  #rail {
    position:fixed; left:0; right:0; bottom:0; z-index:6;
    display:flex; background:var(--void); border-top:1px solid var(--rule);
    padding-bottom:env(safe-area-inset-bottom);
  }
  .tab {
    flex:1 1 0; min-width:0; appearance:none; border:0; background:transparent;
    color:var(--ink-dim); cursor:pointer; padding:11px 4px 10px;
    font:500 9px/1 var(--mono); letter-spacing:.16em; text-transform:uppercase;
    display:flex; flex-direction:column; align-items:center;
    min-height:44px;
  }
  /* THE INDICATOR IS A PSEUDO-ELEMENT, not a child, and that is not a style
     preference. The headless harness models `textContent` as a plain property
     that does not aggregate children, so a tab built as
     `<button><u></u><span>Work</span></button>` reads as an EMPTY label there —
     a browser would concatenate, the stub cannot, and the guard that checks the
     rail's labels would be comparing against ''. One text node, drawn mark. */
  .tab::before { content:""; display:block; width:16px; height:2px;
                 background:currentColor; opacity:.35; margin-bottom:6px; }
  .tab.on { color:var(--ink); }
  .tab.on::before { opacity:1; background:var(--live); }
  .tab:focus-visible { outline:1px solid var(--live); outline-offset:-3px; }

  /* ------------------------------------------------------------- the strips
     ONE PATTERN FOR ALL TEN SURFACES. A row is a fixed-height instrument strip
     hanging off a 2px rail; the rail is the ONLY surface permitted colour, and
     it carries state by SEGMENTATION as well as hue — solid is measured, and
     DASHED is an unbounded authority, a broken rail for a broken boundary.
     Height is `rows x 52px`, independent of how wide the record is: that is
     what collapses 45,728px of wrapped table text (MEASURED 2026-09-12, 54
     phone screens) into a list you can thumb. */
  .strip {
    display:grid; grid-template-columns:2px 1fr auto; gap:0 11px;
    align-items:center; min-height:52px; padding:7px 0;
    border-bottom:1px solid var(--rule); cursor:pointer; width:100%;
    background:transparent; border-left:0; border-right:0; border-top:0;
    color:inherit; font:inherit; text-align:left;
  }
  .strip:focus-visible { outline:1px solid var(--live); outline-offset:-2px; }
  .rail { align-self:stretch; background:var(--ink); opacity:.32; }
  .rail.ok { opacity:.32; }
  .rail.degraded { background:var(--degraded); opacity:1; }
  .rail.down { background:var(--down); opacity:1; }
  /* `unknown` is ABSENCE, not failure: unsurveyed territory on a map. It can
     never be mistaken for a fault because it has no hue at all. */
  .rail.unknown { background:none; opacity:1;
    background-image:repeating-linear-gradient(45deg,
      var(--rule) 0 1px, transparent 1px 6px); }
  /* A broken boundary, drawn broken. Seven of these down the agent list is
     unskimmable and costs no saturation. */
  .rail.unbounded { background:none; opacity:1;
    background-image:repeating-linear-gradient(180deg,
      var(--ink-dim) 0 4px, transparent 4px 9px); }

  .who { min-width:0; }
  .who b { display:block; font:500 13px/1.3 var(--mono); color:var(--ink);
           overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .who s { display:block; text-decoration:none; font:400 10px/1.4 var(--mono);
           letter-spacing:.1em; text-transform:uppercase; color:var(--ink-dim);
           overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .metric { text-align:right; font:500 13px/1.2 var(--mono); color:var(--ink); white-space:nowrap; }
  .metric s { display:block; text-decoration:none; font:400 10px/1.4 var(--mono);
              letter-spacing:.1em; text-transform:uppercase; color:var(--ink-dim); }

  /* --------------------------------------------------------------- sections */
  section { margin:0 0 22px; }
  h2 { margin:0 0 2px; font:500 10px/1 var(--mono); letter-spacing:.22em;
       text-transform:uppercase; color:var(--ink-dim);
       display:flex; align-items:center; gap:9px; }
  /* Hairlines bleed past their content to the viewport edge — a rangefinder
     frame rather than a card. */
  h2::after { content:""; flex:1 1 auto; height:1px; background:var(--rule); }
  .note { margin:6px 0 0; font:400 12px/1.5 var(--sans); color:var(--ink-dim); }
  .note b { color:var(--ink); font-weight:500; }
  .empty { padding:14px 0; color:var(--ink-dim); font:400 12px/1.5 var(--sans); }

  /* ------------------------------------------------------------- the sheet
     A 2,308-CHARACTER FIELD NEVER APPEARS IN A LIST. The strip shows a derived
     line; the whole record lives here, structured fields first and long text
     last, so prose can never push the facts below the fold. */
  #sheet { position:fixed; inset:0; z-index:9; background:var(--void);
           display:flex; flex-direction:column; }
  #sheethead { flex:none; display:flex; align-items:center; gap:10px;
               padding:calc(env(safe-area-inset-top) + 10px) 14px 10px;
               border-bottom:1px solid var(--rule); }
  #sheettitle { font:500 13px/1.3 var(--mono); color:var(--ink);
                overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  #sheetclose { margin-left:auto; appearance:none; background:transparent;
                border:1px solid var(--rule); color:var(--ink-dim);
                font:500 10px/1 var(--mono); letter-spacing:.16em;
                text-transform:uppercase; padding:10px 12px; cursor:pointer;
                min-height:44px; }
  #sheetbody { flex:1 1 auto; overflow-y:auto;
               padding:12px 14px calc(env(safe-area-inset-bottom) + 20px); }
  .field { padding:9px 0; border-bottom:1px solid var(--rule); }
  .field s { display:block; text-decoration:none; font:400 10px/1.4 var(--mono);
             letter-spacing:.18em; text-transform:uppercase; color:var(--ink-dim); }
  .field p { margin:3px 0 0; font:400 13px/1.5 var(--mono); color:var(--ink);
             overflow-wrap:anywhere; white-space:pre-wrap; }

  /* ------------------------------------------------------------------- gate */
  #gate { max-width:340px; margin:8vh auto 0; }
  form { display:flex; flex-direction:column; gap:9px; margin:14px 0 0; }
  /* THE iOS ZOOM FLOOR. Anything under 16px makes Safari zoom the viewport on
     focus, which throws a fixed bottom bar off screen and is the single most
     app-breaking detail on a phone. */
  input {
    font:400 16px/1.3 var(--mono); color:var(--ink); background:transparent;
    border:1px solid var(--rule); padding:13px 12px; min-height:44px;
  }
  input:focus { outline:none; border-color:var(--ink-dim); }
  button.go { appearance:none; background:var(--ink); color:var(--void);
              border:0; padding:14px; min-height:44px; cursor:pointer;
              font:500 11px/1 var(--mono); letter-spacing:.18em; text-transform:uppercase; }
  button.ghost { appearance:none; background:transparent; color:var(--ink-dim);
                 border:1px solid var(--rule); padding:13px; min-height:44px;
                 cursor:pointer; font:500 11px/1 var(--mono);
                 letter-spacing:.18em; text-transform:uppercase; }
  #status { margin:12px 0 0; font:400 12px/1.5 var(--sans); color:var(--ink-dim); }
  .bad { color:var(--down); }

  /* -------------------------------------------------------------- the tiles
     ONE HERO FIGURE PER VIEWPORT. If there are two, there are none. */
  .tiles { display:grid; grid-template-columns:repeat(2, 1fr); gap:1px;
           background:var(--rule); border:1px solid var(--rule); margin:0 0 8px; }
  .tile { background:var(--void); padding:12px 12px 11px; min-width:0; }
  .tile s { display:block; text-decoration:none; font:400 10px/1 var(--mono);
            letter-spacing:.18em; text-transform:uppercase; color:var(--ink-dim); }
  .tile b { display:block; margin-top:7px; font:500 20px/1 var(--mono); color:var(--ink); }
  .tile.hero b { font-size:34px; line-height:.9; letter-spacing:-.01em; }
  .tile i { display:block; margin-top:6px; font:400 11px/1.4 var(--sans);
            font-style:normal; color:var(--ink-dim); }

  @media (min-width:720px) {
    main { max-width:900px; margin:0 auto; width:100%; padding-bottom:24px; }
    #rail { position:static; border-top:0; border-bottom:1px solid var(--rule);
            max-width:900px; margin:0 auto; }
    #app { padding-bottom:0; }
    .tiles { grid-template-columns:repeat(4, 1fr); }
  }

  /* NOTHING LOOPS, and nothing here carries information. Motion only confirms
     freshness, which is also printed as a timestamp — so with motion disabled
     the page loses no content at all. */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration:.01ms !important;
                             transition-duration:.01ms !important; }
    #freshbar.tick i { animation:none; transform:translateX(0); }
  }
</style>
</head>
<body>
<div id="app">

<header id="top">
  <!-- The same path `/icon.svg` serves, inline and taking `currentColor`, so the
       home-screen icon and the header cannot drift apart. -->
  <svg class="mark" viewBox="0 0 24 24" aria-hidden="true">
    <path fill="currentColor" fill-rule="evenodd"
     d="M3.5 2H20.5A1.5 1.5 0 0 1 22 3.5V7.5A1.5 1.5 0 0 1 20.5 9H13.4L12 6.9L10.6 9H3.5
        A1.5 1.5 0 0 1 2 7.5V3.5A1.5 1.5 0 0 1 3.5 2Z
        M9 3.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 1 0 0-3.8Z
        M15 3.6a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 1 0 0-3.8Z
        M3.5 10.5H20.5A1.5 1.5 0 0 1 22 12V14A1.5 1.5 0 0 1 20.5 15.5H3.5
        A1.5 1.5 0 0 1 2 14V12A1.5 1.5 0 0 1 3.5 10.5Z
        M6.5 17H17.5A1.5 1.5 0 0 1 19 18.5V20.5A1.5 1.5 0 0 1 17.5 22H6.5
        A1.5 1.5 0 0 1 5 20.5V18.5A1.5 1.5 0 0 1 6.5 17Z"/>
  </svg>
  <span class="wordmark">STACK<b>OWL</b></span>
  <!-- THE ONLY ELEMENT PERMITTED `--live`, and it earns the hue by printing the
       timestamp it is derived from immediately beside itself. No pixel may
       imply a measurement it cannot cite. -->
  <span id="fresh"><span id="freshbar"><i></i></span><span id="freshat">—</span></span>
</header>

<main>
  <section id="gate">
    <h2>Sign in</h2>
    <form id="loginform">
      <input id="username" type="text" placeholder="username"
             autocomplete="username" spellcheck="false">
      <input id="password" type="password" placeholder="password"
             autocomplete="current-password" spellcheck="false">
      <button class="go" type="submit">Sign in</button>
      <button class="ghost" type="button" id="forget">Sign out</button>
    </form>
    <p class="note" id="defaultwarn" hidden></p>
    <p class="note">Set <b>control_plane.username</b> and <b>control_plane.password</b>
       in stackowl.yaml. The session is kept for this tab only and never written
       to a cookie.</p>
    <p class="note" id="status"></p>
  </section>

  <!-- FIVE DESTINATIONS, BUILT FROM `PANELS` AND NEVER LISTED HERE. The buttons
       are created by the script from each panel's `dest`, so this element is a
       mount point rather than a second enumeration. -->
  <nav id="rail"></nav>

  <div class="dest" id="deststatus" hidden>
    <!-- NOT A `<section>`, and that is load-bearing. `test_the_panel_list_is_written_ONCE`
         derives the panel set from `<section id=… hidden>` and requires it to equal
         `PANELS` exactly. This block has no route behind it — it is a fold over
         payloads already fetched — so dressing it as a panel would either break that
         bijection or force a fake entry into the one enumeration this page keeps. -->
    <div class="block" id="brief" hidden>
      <h2>Now</h2>
      <div class="tiles" id="brieftiles"></div>
      <p class="note" id="briefnote"></p>
    </div>
    <section id="health" hidden>
      <h2>Subsystems</h2>
      <div id="healthrows"></div>
      <p class="note" id="healthnote"></p>
    </section>
  </div>

  <div class="dest" id="destfleet" hidden>
    <section id="agents" hidden>
      <h2>Owls</h2>
      <div id="agentrows"></div>
      <p class="note" id="agentnote"></p>
    </section>
    <section id="skills" hidden>
      <h2>Who owns which skill</h2>
      <div id="skillrows"></div>
    </section>
  </div>

  <div class="dest" id="destwork" hidden>
    <section id="tasks" hidden>
      <h2>Work in flight</h2>
      <div id="taskrows"></div>
      <p class="note" id="tasknote"></p>
      <p class="note">LIVE work only — a row the loop has finished with is not
         here. <b>Blocked</b> says why a pending row is not moving:
         <b>terminal_parent</b> means its parent finished, so the loop will never
         run it, and that is correct rather than stuck.</p>
    </section>
    <section id="schedules" hidden>
      <h2>Schedules</h2>
      <div id="schedulerows"></div>
      <p class="note" id="schedulenote"></p>
    </section>
  </div>

  <div class="dest" id="destmind" hidden>
    <section id="memory" hidden>
      <h2>Learned from doing the work</h2>
      <div id="curatedrows"></div>
      <div id="lessonrows"></div>
      <p class="note" id="memorynote"></p>
    </section>
    <section id="interactions" hidden>
      <h2>How the agents interact</h2>
      <div id="edgerows"></div>
      <p class="note" id="edgenote"></p>
    </section>
  </div>

  <div class="dest" id="destsystem" hidden>
    <section id="config" hidden>
      <h2>Settings</h2>
      <div id="configrows"></div>
      <p class="note">Credential-shaped values are masked before they leave the
         process, by the same predicate the log redaction uses.</p>
    </section>
  </div>
</main>

<!-- THE DETAIL SURFACE. One sheet, reused by every strip on every surface:
     ten renderers, one detail view, and no per-surface layout to keep honest. -->
<div id="sheet" hidden>
  <header id="sheethead">
    <span id="sheettitle"></span>
    <button id="sheetclose" type="button">Close</button>
  </header>
  <div id="sheetbody"></div>
</div>

</div>
<script>
(function () {
  var KEY = "stackowl.control.token";
  function $(id) { return document.getElementById(id); }
  function say(t) { $("status").textContent = t; }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null && text !== "") { n.textContent = String(text); }
    return n;
  }
  function dash(v) {
    return v === null || v === undefined || v === "" ? "—" : String(v);
  }
  function short(v, n) {
    var s = dash(v);
    return s.length <= n ? s : s.slice(0, n - 1) + "…";
  }

  // ------------------------------------------------------------ the sheet
  //
  // ONE DETAIL SURFACE FOR TEN LISTS. A strip promotes exactly three facts; the
  // whole record lives here, STRUCTURED FIELDS FIRST AND LONG TEXT LAST, so a
  // 2,308-character memory entry can never push the facts below the fold.
  // MEASURED 2026-09-12: rendered inline as table cells, the same payloads are
  // 45,728px on a phone — fifty-four screens — and one memory row is 1,691px by
  // itself. A strip is 52px whatever the record weighs.
  function openSheet(title, fields) {
    $("sheettitle").textContent = title;
    var body = $("sheetbody");
    body.textContent = "";
    var longOnes = [];
    fields.forEach(function (f) {
      var v = dash(f[1]);
      if (v.length > 120) { longOnes.push(f); return; }
      var d = el("div", "field");
      d.appendChild(el("s", null, f[0]));
      d.appendChild(el("p", null, v));
      body.appendChild(d);
    });
    longOnes.forEach(function (f) {
      var d = el("div", "field");
      d.appendChild(el("s", null, f[0]));
      d.appendChild(el("p", null, dash(f[1])));
      body.appendChild(d);
    });
    $("sheet").hidden = false;
  }
  $("sheetclose").addEventListener("click", function () { $("sheet").hidden = true; });

  // ------------------------------------------------------------- the strip
  //
  // The rail is the ONLY surface in this page permitted colour, and it carries
  // state by SEGMENTATION as well as hue: solid is measured, a 45-degree hatch
  // is `unknown` (absence, never failure), and a dashed rail is an unbounded
  // authority — a broken boundary, drawn broken.
  function strip(into, kind, title, sub, metric, unit, fields) {
    var b = el("button", "strip");
    var rail = el("span", "rail " + (kind || "ok"));
    var who = el("span", "who");
    who.appendChild(el("b", null, title));
    who.appendChild(el("s", null, sub));
    var m = el("span", "metric", metric);
    if (unit) { m.appendChild(el("s", null, unit)); }
    b.appendChild(rail);
    b.appendChild(who);
    b.appendChild(m);
    b.addEventListener("click", function () { openSheet(String(title), fields || []); });
    into.appendChild(b);
    return b;
  }

  function fill(id, rows, render, emptyText) {
    var host = $(id);
    host.textContent = "";
    if (!rows || !rows.length) {
      host.appendChild(el("p", "empty", emptyText || "nothing here"));
      return;
    }
    rows.forEach(function (r) { render(host, r); });
  }

  // ------------------------------------------------------------------ PANELS
  //
  // THE SINGLE ENUMERATION. Fetch, render, destination, error-hide and the
  // sign-out sweep all derive from this one array; `dest` is a LABEL, so the
  // wrapper's element id ("dest" + lowercase) and the tab's text both come from
  // it and there is nothing to keep in sync.
  var PANELS = [
    { id: "health",       path: "/api/v1/health",       render: renderHealth,       dest: "Status" },
    { id: "agents",       path: "/api/v1/agents",       render: renderAgents,       dest: "Fleet" },
    { id: "skills",       path: "/api/v1/skills",       render: renderSkills,       dest: "Fleet" },
    { id: "tasks",        path: "/api/v1/tasks",        render: renderTasks,        dest: "Work" },
    { id: "schedules",    path: "/api/v1/schedules",    render: renderSchedules,    dest: "Work" },
    { id: "memory",       path: "/api/v1/memory",       render: renderMemory,       dest: "Mind" },
    { id: "interactions", path: "/api/v1/interactions", render: renderInteractions, dest: "Mind" },
    { id: "config",       path: "/api/v1/config",       render: renderConfig,       dest: "System" }
  ];

  function destId(label) { return "dest" + label.toLowerCase(); }
  function destinations() {
    var seen = [];
    PANELS.forEach(function (p) {
      if (seen.indexOf(p.dest) === -1) { seen.push(p.dest); }
    });
    return seen;
  }

  var TABS = [];
  var CURRENT = null;

  function showDest(label) {
    destinations().forEach(function (d) {
      var n = $(destId(d));
      // `hidden` IS THE ONLY VISIBILITY ACTUATOR ON THIS PAGE, deliberately.
      // Every execution assertion reads `.hidden`, and the stub DOM has no CSS
      // and no parent/child containment — so the moment navigation moves to a
      // class, all of those tests stay green while a browser shows five
      // destinations at once. CSS here decorates what is already un-hidden.
      if (n) { n.hidden = d !== label; }
    });
    TABS.forEach(function (t) {
      t.className = t._dest === label ? "tab on" : "tab";
    });
    CURRENT = label;
  }

  function buildRail() {
    if (TABS.length) { return; }
    var rail = $("rail");
    destinations().forEach(function (d) {
      var b = el("button", "tab", d);
      b._dest = d;
      b.addEventListener("click", function () { showDest(d); });
      rail.appendChild(b);
      TABS.push(b);
    });
  }

  // --------------------------------------------------------------- renderers
  function healthKind(s) {
    if (s === "down") { return "down"; }
    if (s === "degraded") { return "degraded"; }
    if (s === "unknown") { return "unknown"; }
    return "ok";
  }

  function renderHealth(p) {
    fill("healthrows", p.subsystems, function (host, sub) {
      strip(host, healthKind(sub.status), sub.name, sub.status,
            sub.latency_ms === null || sub.latency_ms === undefined
              ? "—" : Math.round(sub.latency_ms), "ms",
            [["status", sub.status], ["latency", sub.latency_ms], ["message", sub.message],
             ["remedy", sub.remedy]]);
    }, "no subsystem reported");
    var bad = (p.subsystems || []).filter(function (s) { return s.status !== "ok"; });
    $("healthnote").textContent = bad.length
      ? bad.length + " not ok right now"
      : "All " + (p.subsystems || []).length + " ok at this instant. This is a "
        + "SNAPSHOT, not a history — a subsystem that flapped an hour ago reads ok here.";
    $("health").hidden = false;
  }

  function renderAgents(p) {
    fill("agentrows", p.agents, function (host, owl) {
      // `unbounded` is an AUTHORITY verdict, not a health one, so it gets form
      // rather than hue: a dashed rail. Colouring it red would be an invented
      // alarm about something that is a configuration fact.
      strip(host, owl.unbounded ? "unbounded" : "ok",
            owl.display_name || owl.name, owl.role ? short(owl.role, 42) : owl.model_tier,
            owl.unbounded ? "UNBOUNDED" : "bounded", owl.model_tier,
            [["name", owl.name], ["role", owl.role], ["tier", owl.model_tier],
             ["lifecycle", owl.lifecycle], ["origin", owl.origin],
             ["in flight", owl.in_flight], ["recent turns", owl.recent_turns],
             ["skills on card", owl.skills_on_card], ["skills owned", owl.skills_owned],
             ["authority", owl.unbounded ? "UNBOUNDED" : "bounded"],
             ["bounds", owl.bounds], ["creation ceiling", owl.creation_ceiling]]);
    }, "no agents registered");
    var un = (p.agents || []).filter(function (a) { return a.unbounded; }).length;
    $("agentnote").textContent = un
      ? un + " of " + (p.agents || []).length + " carry no authority ceiling — a "
        + "dashed rail is a broken boundary, not a fault."
      : "every agent carries an authority ceiling";
    $("agents").hidden = false;
  }

  function renderSkills(p) {
    fill("skillrows", p.owls, function (host, card) {
      strip(host, "ok", card.owl, short(card.skills, 46), card.count, "skills",
            [["owl", card.owl], ["count", card.count], ["skills", card.skills]]);
    }, "no skill ownership recorded");
    $("skills").hidden = false;
  }

  function blockedKind(b) { return b === "none" ? "ok" : "unknown"; }

  function renderTasks(p) {
    fill("taskrows", p.tasks, function (host, task) {
      strip(host, blockedKind(task.blocked), task.goal ? short(task.goal, 48) : task.task_id,
            (task.owl_name || "unassigned") + " · " + task.status,
            task.attempt_count + "/" + task.max_attempts, "attempts",
            [["task", task.task_id], ["owl", task.owl_name], ["status", task.status],
             ["blocked", task.blocked], ["attempts", task.attempt_count + "/" + task.max_attempts],
             ["next attempt", task.next_attempt_at], ["goal", task.goal],
             ["last error", task.last_error]]);
    }, "no live work — the loop has finished everything it was given");
    var parts = [(p.tasks || []).length + " live"];
    if (p.dead_lettered) {
      parts.push(p.dead_lettered + " dead-lettered, not shown (newest "
                 + dash(p.newest_dead_letter_at).slice(0, 19) + ")");
    }
    if (p.truncated) { parts.push("TRUNCATED — more live rows than this shows"); }
    if (p.unseen_other_owner) { parts.push(p.unseen_other_owner + " belong to another owner"); }
    $("tasknote").textContent = parts.join(" · ");
    $("tasks").hidden = false;
  }

  function renderSchedules(p) {
    fill("schedulerows", p.schedules, function (host, job) {
      var kind = job.status === "failed" ? "down" : (job.enabled ? "ok" : "unknown");
      strip(host, kind, job.job_id, job.handler + " · " + job.schedule,
            dash(job.next_run_at).slice(0, 16), job.enabled ? "next" : "disabled",
            [["job", job.job_id], ["handler", job.handler], ["schedule", job.schedule],
             ["status", job.status], ["enabled", String(job.enabled)],
             ["next run", job.next_run_at], ["last run", job.last_run_at],
             ["failures", job.failure_count], ["last error", job.last_error]]);
    }, "no jobs scheduled");
    var rows = p.schedules || [];
    var off = rows.filter(function (j) { return !j.enabled; }).length;
    var bad = rows.filter(function (j) { return j.status === "failed"; }).length;
    $("schedulenote").textContent = rows.length + " jobs · " + off + " disabled · "
      + bad + " failed. Status is the last RUN's, not an outcome over time.";
    $("schedules").hidden = false;
  }

  function renderMemory(p) {
    fill("curatedrows", p.curated, function (host, cur) {
      strip(host, "ok", cur.target, "curated", String(dash(cur.entries).length), "chars",
            [["target", cur.target], ["entries", cur.entries]]);
    }, "nothing curated");
    fill("lessonrows", p.lessons, function (host, lesson) {
      strip(host, "ok", short(lesson.content, 52), lesson.source || lesson.source_ref,
            dash(lesson.lesson_id).slice(0, 8), "id",
            [["lesson", lesson.lesson_id], ["source", lesson.source], ["ref", lesson.source_ref],
             ["at", lesson.at], ["content", lesson.content]]);
    }, "nothing learned yet");
    $("memorynote").textContent = (p.lessons || []).length + " of " + dash(p.total)
      + " shown — this is a window, not the store.";
    $("memory").hidden = false;
  }

  function renderInteractions(p) {
    fill("edgerows", p.edges, function (host, edge) {
      strip(host, edge.outcome === "ok" || edge.outcome === "completed" ? "ok" : "unknown",
            // `·` and `—`, never an arrow: the markup guard pins the non-ASCII
            // set to three characters so a homoglyph cannot slip in unnoticed.
            (edge.from_owl || "—") + " · " + (edge.to_owl || "—"),
            edge.kind + " · " + dash(edge.at).slice(0, 19), edge.outcome, "",
            [["at", edge.at], ["kind", edge.kind], ["from", edge.from_owl], ["to", edge.to_owl],
             ["outcome", edge.outcome], ["ref", edge.ref], ["detail", edge.detail]]);
    }, "no recorded interactions");
    var g = p.gaps || {};
    var parts = [(p.edges || []).length + " edges"];
    parts.push("newest delegation " + dash(g.newest_delegation_recorded).slice(0, 10));
    if (g.delegations_without_a_target) {
      parts.push(g.delegations_without_a_target + " with no recorded target");
    }
    if (g.delegations_without_a_caller) {
      parts.push(g.delegations_without_a_caller + " with no recoverable caller");
    }
    $("edgenote").textContent = parts.join(" · ");
    $("interactions").hidden = false;
  }

  function renderConfig(p) {
    fill("configrows", p.settings, function (host, setting) {
      strip(host, setting.masked ? "unknown" : "ok", setting.key, setting.masked ? "masked" : "set",
            short(setting.value, 18), "", [["key", setting.key], ["value", setting.value],
             ["masked", String(setting.masked)]]);
    }, "nothing configured");
    $("config").hidden = false;
  }

  // ------------------------------------------------------------- the brief
  //
  // A FOLD OVER PAYLOADS ALREADY IN HAND — no route, no extra latency, and it
  // renders as each one lands rather than waiting on the slowest. `health` has a
  // MEASURED 612ms floor and a 15s structural worst case, so anything that
  // blocked on it would be the first thing a viewer waits for.
  //
  // AND IT STATES WHAT IT CANNOT SEE. Every route is a snapshot: MEASURED
  // 2026-09-12, the health sweep reported unhealthy subsystems 49 times that day
  // and 114 the day before, while this page's health payload read 15/15 ok. A
  // number with no time axis beside it is not a verdict, so the caption says so.
  function tile(host, label, value, caption, hero) {
    var t = el("div", hero ? "tile hero" : "tile");
    t.appendChild(el("s", null, label));
    t.appendChild(el("b", null, value));
    if (caption) { t.appendChild(el("i", null, caption)); }
    host.appendChild(t);
  }

  function renderBrief(by) {
    var host = $("brieftiles");
    host.textContent = "";
    var h = by.health, t = by.tasks, a = by.agents, s = by.schedules;
    if (h) {
      var notOk = (h.subsystems || []).filter(function (x) { return x.status !== "ok"; }).length;
      tile(host, "Subsystems", (h.subsystems || []).length - notOk + "/"
           + (h.subsystems || []).length, notOk ? notOk + " not ok" : "at this instant", true);
    }
    if (t) {
      tile(host, "Live work", (t.tasks || []).length,
           t.dead_lettered ? t.dead_lettered + " ended without succeeding" : "nothing ended badly");
    }
    if (a) {
      var un = (a.agents || []).filter(function (x) { return x.unbounded; }).length;
      tile(host, "Owls", (a.agents || []).length,
           un ? un + " with no authority ceiling" : "all bounded");
    }
    if (s) {
      var rows = s.schedules || [];
      tile(host, "Jobs", rows.length,
           rows.filter(function (j) { return j.status === "failed"; }).length + " failed at last run");
    }
    $("briefnote").textContent = "Every figure here is a SNAPSHOT. None of these "
      + "routes carries a time axis, so a subsystem that flapped an hour ago and "
      + "recovered is invisible above.";
    $("brief").hidden = false;
  }

  // ------------------------------------------------------------------ fetch
  function get(path, token) {
    return fetch(path, { headers: { "Authorization": "Bearer " + token } })
      .then(function (r) {
        if (r.status === 503) { return r.json(); }
        if (!r.ok) { throw new Error("HTTP " + r.status); }
        return r.json();
      });
  }

  function stamp() {
    $("freshat").textContent = new Date().toLocaleTimeString();
    var bar = $("freshbar");
    bar.className = "";
    bar.className = "tick";
  }

  function load(token) {
    say("reading the platform…");
    Promise.allSettled(PANELS.map(function (p) { return get(p.path, token); }))
      .then(function (results) {
        var failed = [];
        var by = {};
        results.forEach(function (res, i) {
          var panel = PANELS[i];
          if (res.status === "fulfilled") {
            by[panel.id] = res.value;
            panel.render(res.value);
          } else {
            $(panel.id).hidden = true;
            failed.push(panel.id + ": " + String(res.reason && res.reason.message || res.reason));
          }
        });
        var ok = results.filter(function (r) { return r.status === "fulfilled"; });
        if (!ok.length) { say(failed.join(" · ")); return; }
        renderBrief(by);
        buildRail();
        $("rail").hidden = false;
        showDest(CURRENT || destinations()[0]);
        stamp();
        say(failed.length ? failed.join(" · ") : "");
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
        w.textContent = "Signed in with the DEFAULT credentials (admin/admin). "
          + "Anyone who can reach this address can sign in. Set "
          + "control_plane.username and control_plane.password.";
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
    $("sheet").hidden = true;
    // FROM `PANELS`, never a list written here — and the shell around them too.
    // Hiding eight panels while leaving five wrappers and a tab bar on screen
    // answers "sign out" with an empty app rather than a signed-out one.
    PANELS.forEach(function (p) { $(p.id).hidden = true; });
    destinations().forEach(function (d) {
      var n = $(destId(d));
      if (n) { n.hidden = true; }
    });
    $("brief").hidden = true;
    $("rail").hidden = true;
    say("token forgotten");
  });

  // THE RESUME PATH. A tab that already holds a session renders straight away;
  // this was dead for two days once because it reached for an element the
  // markup had stopped declaring, so it is exercised by its own guard now.
  var stored = null;
  try { stored = sessionStorage.getItem(KEY); } catch (e) { stored = null; }
  if (stored) { load(stored); }
  else { say("sign in to read the platform"); }
})();
</script>
</body>
</html>
"""
