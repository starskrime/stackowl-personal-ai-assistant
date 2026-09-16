// Story 1.5 (AD-36): externalized from offline-summary.html's inline
// <script> -- the enforced `script-src 'self'` has no 'unsafe-inline'. All
// three original `.innerHTML =` assignments (the error message, and the
// success branch, which already avoided it) are replaced with
// replaceChildren() + createElement()/textContent, mirroring the success
// branch's own pre-existing pattern.
(async () => {
  const summaryEl = document.getElementById("summary");
  const params = new URLSearchParams(location.search);
  const itemId = params.get("item") || "";
  try {
    // Same cache name/key shape as bridge_spike/static/sw.js's
    // PUSH_SUMMARY_CACHE / pushSummaryCacheKey() -- Cache Storage is
    // shared per-origin, so this page can read what the service worker
    // wrote without any message-passing.
    const cache = await caches.open("bridge-spike-push-summaries-v1");
    const match = await cache.match(`/__push-summary__/${encodeURIComponent(itemId)}`);
    if (!match) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "No cached summary for this item yet.";
      summaryEl.replaceChildren(empty);
      return;
    }
    const metadata = await match.json();
    const kind = document.createElement("p");
    kind.className = "kind";
    kind.textContent = metadata.kind || "";
    const rendering = document.createElement("p");
    rendering.className = "rendering";
    rendering.textContent = metadata.rendering || "";
    summaryEl.replaceChildren(kind, rendering);
    window.__bridgeOfflineSummaryMetadata = metadata; // check.py reads this via page.evaluate
  } catch (err) {
    const errorEl = document.createElement("p");
    errorEl.className = "empty";
    errorEl.textContent = `Could not read the cached summary: ${err}`;
    summaryEl.replaceChildren(errorEl);
  }
})();
