// Minimal service worker so the PWA installs and opens standalone, PLUS
// (Story 1.3, AD-19) push delivery, a metadata-only cache for the
// away-from-home summary (FR25), and notificationclick routing.
//
// FR26 ("never full content") is enforced structurally here, not just by
// convention: narrowToMetadata() below is the ONLY thing that ever reads a
// push payload, and it reads out exactly four fields -- there is nowhere
// for anything else in the payload to end up cached or shown.

const PUSH_SUMMARY_CACHE = "bridge-spike-push-summaries-v1";
const APP_SHELL_CACHE = "bridge-spike-app-shell-v1";
// Precached so the offline summary page itself opens even when genuinely
// offline (the existing fetch handler below falls back to this cache) --
// the metadata it reads comes from PUSH_SUMMARY_CACHE separately, populated
// per-item by handlePushMetadata() as each push arrives.
const APP_SHELL_URLS = ["/offline-summary.html"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(APP_SHELL_CACHE)
      .then((cache) => cache.addAll(APP_SHELL_URLS))
      .catch((err) => {
        // A precache failure (e.g. offline-summary.html briefly
        // unreachable) must not block installation -- this kit's baseline
        // design is "no offline caching strategy needed beyond don't
        // crash", which skipWaiting() below preserves unconditionally.
        console.warn("bridge-spike sw: app-shell precache failed", err);
      })
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});

// -- push delivery + metadata-only cache (FR25, FR26) -----------------------

function pushSummaryCacheKey(itemId) {
  // Same-origin synthetic request URL used purely as a cache key -- never
  // actually fetched over the network.
  return new Request(`/__push-summary__/${encodeURIComponent(String(itemId))}`);
}

function narrowToMetadata(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  return {
    id: source.id,
    kind: source.kind,
    intensity: source.intensity,
    rendering: source.rendering,
  };
}

async function cacheMetadataOnly(metadata) {
  const cache = await caches.open(PUSH_SUMMARY_CACHE);
  const response = new Response(JSON.stringify(metadata), {
    headers: { "Content-Type": "application/json" },
  });
  await cache.put(pushSummaryCacheKey(metadata.id), response);
}

async function handlePushMetadata(metadata) {
  if (metadata.id === undefined) {
    // A malformed/unparseable payload narrows to an id-less metadata object
    // (see narrowToMetadata) -- caching it would collide under the same
    // literal "/__push-summary__/undefined" key as any other id-less push,
    // clobbering whichever cached there first. Skip both caching and
    // notifying rather than risk that.
    console.warn("bridge-spike sw: push payload had no id -- not caching or notifying", metadata);
    return;
  }
  // Cached BEFORE attempting to show a notification: the offline summary
  // must work even on a host that cannot display a native notification at
  // all (see the catch below) -- caching is never conditional on that.
  await cacheMetadataOnly(metadata);
  try {
    await self.registration.showNotification(metadata.rendering || "Bridge", {
      body: metadata.rendering || "",
      tag: String(metadata.id),
      data: metadata,
    });
  } catch (err) {
    // Some hosts (e.g. a headless build box with no OS notification
    // backend) cannot display a native notification at all. The metadata is
    // already cached above, so the away-from-home summary still works --
    // this is an unavailable platform capability, not a hidden failure of
    // this code, so it is logged rather than swallowed.
    console.warn("bridge-spike sw: showNotification failed", err);
  }
}

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (err) {
    console.warn("bridge-spike sw: push payload was not valid JSON", err);
    payload = {};
  }
  const metadata = narrowToMetadata(payload);
  event.waitUntil(handlePushMetadata(metadata));
});

// -- notificationclick: home network opens the item, else the cached ------
// -- metadata-only summary (no error page) ---------------------------------

async function isHomeNetworkReachable() {
  try {
    // Any lightweight, unauthenticated, same-origin route works as the
    // reachability probe -- /api/auth/nonce already exists for this purpose
    // on every other authenticated call.
    const response = await fetch("/api/auth/nonce", { cache: "no-store" });
    return response.ok;
  } catch (err) {
    return false;
  }
}

// Pure decision: reachable -> the item's own page; away from home -> the
// cached metadata-only summary (never a browser network-error page). This
// is deliberately split from the `clients.openWindow()` SIDE EFFECT below
// it, because `openWindow()` only works inside an event that carries real
// user-interaction permission (a genuine notificationclick) -- calling it
// from the test-only "message" path (no user gesture) throws
// InvalidAccessError regardless of how correct the decision was. Splitting
// them means the automated check can still fully exercise the real
// reachability check + real URL construction, just without the one browser
// API call that structurally requires a real click.
async function decideNotificationClickTarget(metadata) {
  const reachable = await isHomeNetworkReachable();
  if (reachable) {
    return { action: "opened_item", url: `/?item=${encodeURIComponent(String(metadata.id))}` };
  }
  return {
    action: "opened_offline_summary",
    url: `/offline-summary.html?item=${encodeURIComponent(String(metadata.id))}`,
  };
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const metadata = event.notification.data || {};
  event.waitUntil(
    decideNotificationClickTarget(metadata).then((decision) => self.clients.openWindow(decision.url))
  );
});

// Test-only seam for bridge_spike/check.py: there is no CDP equivalent of
// ServiceWorker.deliverPushMessage for simulating a REAL OS notification
// click (clicking a native system notification is OS chrome, outside any
// browser's own automation surface, and clients.openWindow() itself refuses
// to run outside a real user-gesture event like notificationclick). This
// message listener calls the exact same decideNotificationClickTarget() the
// real notificationclick listener calls above, with a fabricated `metadata`
// object shaped like a real notification's `.data` field -- so the
// automated check exercises the real reachability decision and real target
// URL, not a second reimplementation. It reports the decision back instead
// of acting on it.
self.addEventListener("message", (event) => {
  if (!event.data || event.data.type !== "bridge-spike-test-notification-click") {
    return;
  }
  const replyTo = event.source;
  event.waitUntil(
    decideNotificationClickTarget(event.data.metadata || {}).then((decision) => {
      if (replyTo) {
        replyTo.postMessage({ type: "bridge-spike-test-notification-click-result", result: decision });
      }
    })
  );
});
