---
name: web-automation
description: Use when a task requires driving a website — navigating pages, reading rendered content, filling forms, clicking elements, or extracting structured data from a live page.
when_to_use: When the target information or action lives behind a browser interaction that a plain HTTP fetch cannot reach — e.g. a login-gated page, a JS-rendered table, a multi-step form, or a page that requires clicking before data appears.
version: 0.1.0
tags: [browser, web, automation, extraction, forms]
author: stackowl-builtin
license: MIT
---

# Web Automation

Static fetches only see the HTML the server sends on first load. Pages that
render content via scripts, require interaction before revealing data, or sit
behind a session must be driven with browser tools. This skill enforces a
snapshot-before-act, verify-after-act discipline so that no action is reported
as successful without evidence that the page actually reached the expected state.

## Steps

1. **Navigate to the target URL with `browser_navigate`.** Pass the full URL.
   Wait for the page to signal readiness before proceeding; if the page is
   slow, call `browser_wait_for` with an appropriate selector or timeout.

2. **Take a structural snapshot with `browser_snapshot`.** Read the snapshot
   to understand the live DOM — element labels, form fields, button text, and
   any dynamic content that has already loaded. Never act on assumptions from
   prior knowledge of a site; always read the current snapshot first.

3. **Interact using `browser_click` and `browser_type` as needed.** Fill a form
   by typing into each field with `browser_type` and clicking the submit control
   with `browser_click`; use `browser_press` for keystrokes like Enter. Identify
   targets from the snapshot (use accessible labels or stable selectors, not
   positional offsets). Chain interactions in the order the page expects them.

4. **Extract data with `browser_extract` (or re-read with `browser_snapshot`
   after interaction); use `browser_eval_js` for complex structured extraction.**
   For images, use `browser_get_images`; to capture what the page shows, use
   `browser_screenshot`. Confirm the extracted data is non-empty and on-topic
   before treating it as the final result.

5. **Close or clean up the session when done.** Call `browser_close` if a
   long-lived browser context was opened, so it does not leak into subsequent
   turns.

## Verification

Before reporting the outcome:

- Re-take a snapshot after every significant interaction and confirm the page
  reached the expected state (correct URL, expected element visible, form
  confirmation shown) — do not rely on a click or submit having "worked"
  without re-checking.
- Confirm extracted data is non-empty, correctly typed, and clearly comes from
  the target page rather than a stale snapshot or an error page.
- If a navigation redirected to an unexpected URL (e.g. a login wall), surface
  that fact rather than silently returning empty results.
- Never claim a form was submitted or a button was clicked if the post-action
  snapshot does not confirm the expected outcome.

## Pitfalls

- **Acting on a stale snapshot.** Always re-snapshot after navigation or
  interaction before reading element state. A snapshot taken before a click is
  useless for confirming the click worked.
- **Assuming a click succeeded without re-checking.** Buttons can be disabled,
  overlays can intercept clicks, and JS can reset form state. Only a
  post-action snapshot confirms success.
- **Leaking browser sessions.** If `browser_navigate` opens a context,
  `browser_close` must be called when the task ends, even on failure paths.
- **Fragile selectors.** Prefer accessible labels (ARIA, visible text) over
  index-based or pixel-position selectors that break when layouts change.
- **Ignoring redirects.** A navigation that lands on a different URL than
  requested (login page, CAPTCHA, error page) must be surfaced, not silently
  treated as the intended page.
