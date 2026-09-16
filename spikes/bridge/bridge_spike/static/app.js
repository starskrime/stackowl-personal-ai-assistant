// Passkey registration/sign-in, the non-extractable WebCrypto device key,
// IndexedDB token storage, the request-signing helper, and the
// device-approval screen (Story 1.2). Everything the UI buttons below call
// is exposed on `window.BridgeAuth` so the kit's own automated check
// (bridge_spike/check.py) can drive these exact code paths through a real
// browser's real WebAuthn/WebCrypto/IndexedDB APIs via `page.evaluate`,
// rather than re-implementing them a second time just for the check.
(() => {
  "use strict";

  const DB_NAME = "bridge-spike";
  const DB_STORE = "device";

  // -- byte/base64 helpers ------------------------------------------------
  //
  // Two distinct encodings are in play on purpose: WebAuthn's own JSON
  // fields (challenge, credential ids, clientDataJSON, ...) are base64URL
  // per the spec and per py_webauthn's `options_to_json`/verify functions.
  // This kit's OWN endpoints (device_public_key, the request signature)
  // instead use plain base64, matching Python's `base64.b64decode(...,
  // validate=True)` on the server side. Mixing them up silently corrupts
  // exactly one byte in sixty-four, so they are never named the same thing.

  function bufToBase64(buf) {
    const bytes = new Uint8Array(buf);
    let binary = "";
    for (const b of bytes) binary += String.fromCharCode(b);
    return btoa(binary);
  }

  function bufToBase64url(buf) {
    return bufToBase64(buf).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function base64urlToBuf(b64url) {
    let b64 = String(b64url).replace(/-/g, "+").replace(/_/g, "/");
    while (b64.length % 4) b64 += "=";
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  function base64ToBuf(b64) {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  async function sha256Hex(bytes) {
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }

  // -- IndexedDB: the non-extractable device key + bearer token -----------
  //
  // A CryptoKey created with extractable=false can still be stored in and
  // read back from IndexedDB directly (the structured-clone algorithm has a
  // defined CryptoKey serialization) -- the raw private key material is
  // never exposed to this script at any point, before or after persistence.

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        req.result.createObjectStore(DB_STORE, { keyPath: "id" });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function persistDevice(privateKey, token, deviceName) {
    if (navigator.storage && navigator.storage.persist) {
      try {
        await navigator.storage.persist();
      } catch (_err) {
        // Best-effort: persistence is a hint to the browser, not a
        // guarantee, and its absence must not block sign-in from completing.
      }
    }
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(DB_STORE, "readwrite");
      tx.objectStore(DB_STORE).put({ id: 1, privateKey, token, deviceName });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  }

  async function getStoredDevice() {
    const db = await openDb();
    const record = await new Promise((resolve, reject) => {
      const tx = db.transaction(DB_STORE, "readonly");
      const req = tx.objectStore(DB_STORE).get(1);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return record;
  }

  async function clearStoredDevice() {
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(DB_STORE, "readwrite");
      tx.objectStore(DB_STORE).delete(1);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  }

  async function isSignedIn() {
    return Boolean(await getStoredDevice());
  }

  async function getStoredToken() {
    const device = await getStoredDevice();
    return device ? device.token : null;
  }

  // -- request signing ------------------------------------------------

  async function fetchNonce() {
    const response = await fetch("/api/auth/nonce");
    const body = await response.json();
    return body.nonce;
  }

  /** The raw ingredients of a signed request/message: bearer token plus a
   * signature over method/path/timestamp/server-nonce/body-hash, computed
   * by the non-extractable device key. `path` must be the exact request
   * path (no origin, no query string) -- the server signs over the same
   * thing it received on `request.path`. Shared by `signedFetch` below
   * (every signed HTTPS route) AND the WebTransport carrier's own signed
   * auth message (`BridgeStream`), which is not an HTTP request at all and
   * so has no `fetch()` to hang a header on -- both carriers need the exact
   * same token/timestamp/nonce/signature fields, just delivered differently. */
  async function buildSignedFields(method, path, bodyText) {
    const device = await getStoredDevice();
    if (!device) {
      throw new Error("not signed in: no device key/token in IndexedDB");
    }
    const nonce = await fetchNonce();
    const timestamp = String(Math.floor(Date.now() / 1000));
    const bodyHash = await sha256Hex(new TextEncoder().encode(bodyText));
    const message = `${method}\n${path}\n${timestamp}\n${nonce}\n${bodyHash}`;
    const signature = await crypto.subtle.sign(
      { name: "ECDSA", hash: "SHA-256" },
      device.privateKey,
      new TextEncoder().encode(message)
    );
    return { token: device.token, timestamp, nonce, signature: bufToBase64(signature) };
  }

  /** Every authenticated request: bearer token + the signed fields above,
   * carried as headers -- never in the URL (NFR14). `signal` is optional
   * (an `AbortController`'s signal) -- only `BridgeStream`'s SSE carrier
   * uses it, to simulate a dropped connection on demand. */
  async function signedFetch(method, path, bodyObj, { signal } = {}) {
    const bodyText = bodyObj !== undefined ? JSON.stringify(bodyObj) : "";
    const fields = await buildSignedFields(method, path, bodyText);
    const headers = {
      Authorization: `Bearer ${fields.token}`,
      "X-Bridge-Timestamp": fields.timestamp,
      "X-Bridge-Nonce": fields.nonce,
      "X-Bridge-Signature": fields.signature,
    };
    const init = { method, headers, signal };
    if (bodyObj !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = bodyText;
    }
    return fetch(path, init);
  }

  // -- WebAuthn credential <-> JSON -------------------------------------

  function credentialToJson(credential) {
    const response = credential.response;
    const json = {
      id: credential.id,
      rawId: bufToBase64url(credential.rawId),
      type: credential.type,
      response: { clientDataJSON: bufToBase64url(response.clientDataJSON) },
    };
    if (response.attestationObject) {
      json.response.attestationObject = bufToBase64url(response.attestationObject);
    }
    if (response.signature) {
      json.response.authenticatorData = bufToBase64url(response.authenticatorData);
      json.response.signature = bufToBase64url(response.signature);
      if (response.userHandle) {
        json.response.userHandle = bufToBase64url(response.userHandle);
      }
    }
    return json;
  }

  // -- the non-extractable device key + bearer-token issuance -------------

  async function completeDeviceEnrollment(enrollmentTicket, deviceName) {
    const keyPair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
    const publicKeyDer = await crypto.subtle.exportKey("spki", keyPair.publicKey);
    const response = await fetch("/api/device/register-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enrollment_ticket: enrollmentTicket, device_public_key: bufToBase64(publicKeyDer) }),
    });
    if (!response.ok) {
      throw new Error(`device key registration failed: ${response.status}`);
    }
    const body = await response.json();
    await persistDevice(keyPair.privateKey, body.token, body.device_name || deviceName);
    return body;
  }

  // -- passkey registration / authentication -------------------------------

  async function tryRegisterOptions(setupCode) {
    const response = await fetch("/api/webauthn/register/options", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ setup_code: setupCode }),
    });
    return { ok: response.ok, status: response.status };
  }

  async function registerPasskey(setupCode) {
    const optionsResponse = await fetch("/api/webauthn/register/options", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ setup_code: setupCode }),
    });
    if (!optionsResponse.ok) {
      return { ok: false, step: "options", status: optionsResponse.status };
    }
    const options = await optionsResponse.json();
    const publicKey = {
      ...options,
      challenge: base64urlToBuf(options.challenge),
      user: { ...options.user, id: base64urlToBuf(options.user.id) },
      excludeCredentials: (options.excludeCredentials || []).map((c) => ({
        ...c,
        id: base64urlToBuf(c.id),
      })),
    };
    const credential = await navigator.credentials.create({ publicKey });
    const verifyResponse = await fetch("/api/webauthn/register/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential: credentialToJson(credential) }),
    });
    if (!verifyResponse.ok) {
      return { ok: false, step: "verify", status: verifyResponse.status };
    }
    const verifyBody = await verifyResponse.json();
    await completeDeviceEnrollment(verifyBody.enrollment_ticket, "first-device");
    return { ok: true };
  }

  async function authenticatePasskey() {
    const optionsResponse = await fetch("/api/webauthn/authenticate/options", { method: "POST" });
    if (!optionsResponse.ok) {
      return { ok: false, step: "options", status: optionsResponse.status };
    }
    const options = await optionsResponse.json();
    const publicKey = {
      ...options,
      challenge: base64urlToBuf(options.challenge),
      allowCredentials: (options.allowCredentials || []).map((c) => ({ ...c, id: base64urlToBuf(c.id) })),
    };
    const credential = await navigator.credentials.get({ publicKey });
    const verifyResponse = await fetch("/api/webauthn/authenticate/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential: credentialToJson(credential) }),
    });
    if (!verifyResponse.ok) {
      return { ok: false, step: "verify", status: verifyResponse.status };
    }
    const verifyBody = await verifyResponse.json();
    await completeDeviceEnrollment(verifyBody.enrollment_ticket, "first-device");
    return { ok: true };
  }

  // -- second-device (name + matching code) approval -------------------

  async function requestDeviceAccess(deviceName) {
    const response = await fetch("/api/device-requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_name: deviceName }),
    });
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    return { ok: true, ...(await response.json()) };
  }

  async function pollDeviceRequestStatus(requestId) {
    const response = await fetch(`/api/device-requests/${encodeURIComponent(requestId)}`);
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    const body = await response.json();
    if (body.approved && body.enrollment_ticket) {
      await completeDeviceEnrollment(body.enrollment_ticket, "second-device");
    }
    return { ok: true, ...body };
  }

  async function getPendingDeviceRequest() {
    const response = await signedFetch("GET", "/api/device-requests/pending");
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    const body = await response.json();
    return { ok: true, pending: body.pending };
  }

  async function approveDeviceRequest(requestId, code) {
    const response = await signedFetch("POST", "/api/device-requests/approve", {
      request_id: requestId,
      code: code,
    });
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    return { ok: true };
  }

  async function whoAmI() {
    const response = await signedFetch("GET", "/api/whoami");
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    return { ok: true, ...(await response.json()) };
  }

  window.BridgeAuth = {
    isSignedIn,
    getStoredToken,
    clearStoredDevice,
    signedFetch,
    tryRegisterOptions,
    registerPasskey,
    authenticatePasskey,
    requestDeviceAccess,
    pollDeviceRequestStatus,
    getPendingDeviceRequest,
    approveDeviceRequest,
    whoAmI,
    // Exposed so bridge_spike/check.py can sign in a device for its own
    // push/mic checks using a server-minted ticket (mirroring
    // tests/test_server_push_routes.py's _enroll_device() fixture) instead
    // of spending the kit's one-time setup code or re-running a full
    // WebAuthn ceremony already proven by the passkey check step.
    completeDeviceEnrollment,
  };

  // -- push notifications (Story 1.3, AD-19) -------------------------------

  async function getVapidPublicKey() {
    const response = await fetch("/api/push/vapid-public-key");
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    const body = await response.json();
    return { ok: true, key: body.key };
  }

  /** Posts an already-built subscription info object (the shape a real
   * PushSubscription.toJSON() produces: {endpoint, keys:{p256dh,auth}}) to
   * the signed subscribe route. Split out from subscribe() below so
   * bridge_spike/check.py can drive the real server-side store/validate
   * route with a subscription it built itself, pointed at the kit's own
   * local push-service stand-in, without ever calling a real push relay's
   * PushManager.subscribe() from the automated check. */
  async function subscribeWithInfo(subscriptionInfo) {
    const response = await signedFetch("POST", "/api/push/subscribe", {
      endpoint: subscriptionInfo.endpoint,
      keys: { p256dh: subscriptionInfo.keys.p256dh, auth: subscriptionInfo.keys.auth },
    });
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    return { ok: true };
  }

  async function unsubscribeWithInfo(endpoint) {
    const response = await signedFetch("POST", "/api/push/unsubscribe", { endpoint });
    if (!response.ok) {
      return { ok: false, status: response.status };
    }
    return { ok: true, ...(await response.json()) };
  }

  /** The real production path: a real PushManager.subscribe() against the
   * kit's own VAPID public key, then stored via subscribeWithInfo() above.
   * Real device runs (Story 1.6) use this; the automated check does not,
   * to avoid ever contacting a real push relay from a build-host run. */
  async function subscribe() {
    const keyResult = await getVapidPublicKey();
    if (!keyResult.ok) {
      return { ok: false, step: "vapid-key", status: keyResult.status };
    }
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: base64urlToBuf(keyResult.key),
    });
    const subscriptionJson = subscription.toJSON();
    const stored = await subscribeWithInfo(subscriptionJson);
    if (!stored.ok) {
      return { ok: false, step: "store", status: stored.status };
    }
    return { ok: true, endpoint: subscriptionJson.endpoint };
  }

  /** Test-only seam (bridge_spike/check.py): asks the active service worker
   * to run its real notificationclick handler on a fabricated notification
   * `data` object, since no automation surface can simulate a real OS
   * notification click -- see sw.js's "message" listener docstring. */
  async function simulateNotificationClick(metadata, timeoutMs = 5000) {
    const registration = await navigator.serviceWorker.ready;
    if (!registration.active) {
      return { ok: false, error: "no active service worker" };
    }
    const TIMEOUT = Symbol("timeout");
    const result = await new Promise((resolve) => {
      let settled = false;
      function onMessage(event) {
        if (event.data && event.data.type === "bridge-spike-test-notification-click-result") {
          settled = true;
          navigator.serviceWorker.removeEventListener("message", onMessage);
          resolve(event.data.result);
        }
      }
      navigator.serviceWorker.addEventListener("message", onMessage);
      registration.active.postMessage({ type: "bridge-spike-test-notification-click", metadata });
      // A future service-worker regression that stops replying must not
      // hang this call forever -- resolve with a distinguishable timeout
      // sentinel instead.
      setTimeout(() => {
        if (settled) return;
        navigator.serviceWorker.removeEventListener("message", onMessage);
        resolve(TIMEOUT);
      }, timeoutMs);
    });
    if (result === TIMEOUT) {
      return { ok: false, error: "timeout" };
    }
    return { ok: true, result };
  }

  window.BridgePush = {
    getVapidPublicKey,
    subscribe,
    subscribeWithInfo,
    unsubscribeWithInfo,
    simulateNotificationClick,
  };

  // -- microphone (Story 1.3, NFR29 scope: capture + level meter) ---------

  /** Captures live microphone audio for `durationMs`, reporting a 0..1 peak
   * level via a Web Audio AnalyserNode -- exposed for both the on-page level
   * meter (below) and bridge_spike/check.py's automated proof that captured
   * audio is non-silent. Always stops every track before returning/throwing,
   * so a failed or check-driven capture never leaves the mic indicator lit. */
  async function captureLevel(durationMs = 500) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    try {
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();
      // A new AudioContext starts "suspended" under most browsers' autoplay
      // policy until a user gesture resumes it -- resume() is safe to call
      // even when already running, and without it captureLevel would
      // silently read all-zero samples on a page that opened this without a
      // click (e.g. bridge_spike/check.py's automated capture).
      await audioContext.resume();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      let peak = 0;
      const start = performance.now();
      while (performance.now() - start < durationMs) {
        analyser.getByteTimeDomainData(data);
        for (const sample of data) {
          peak = Math.max(peak, Math.abs(sample - 128) / 128);
        }
        // eslint-disable-next-line no-await-in-loop
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
      await audioContext.close();
      return { ok: true, level: peak };
    } finally {
      stream.getTracks().forEach((track) => track.stop());
    }
  }

  async function permissionState() {
    if (!navigator.permissions || !navigator.permissions.query) {
      return "unknown";
    }
    try {
      const status = await navigator.permissions.query({ name: "microphone" });
      return status.state;
    } catch (err) {
      console.warn("bridge-spike app: navigator.permissions.query failed", err);
      return "unknown";
    }
  }

  window.BridgeMic = {
    captureLevel,
    permissionState,
  };

  // -- live stream: WebTransport-first, automatic SSE fallback (Story 1.4, --
  // -- AD-9, AD-11, AD-12, AD-14, AD-31, NFR11, NFR13, NFR14, NFR46) --------
  //
  // One client store per browser (AD-31): tabs elect a leader via Web Locks
  // (`navigator.locks`) and the leader alone holds the live carrier
  // connection. Every envelope the leader receives is mirrored to follower
  // tabs over `BroadcastChannel`, and the resume cursor is kept in
  // `localStorage` (synchronous, shared across tabs of this origin) so a
  // newly-elected leader -- whether at first load or after the previous
  // leader tab closed -- resumes from exactly where the browser last was,
  // never from zero: "no cursor gap" on hand-off.

  const STREAM_LEADER_LOCK = "bridge-spike-stream-leader";
  const STREAM_CURSOR_STORAGE_KEY = "bridge-spike-stream-cursor";
  const STREAM_BROADCAST_CHANNEL_NAME = "bridge-spike-stream";
  const WEBTRANSPORT_PATH = "/bridge-stream";
  // "Stale within one heartbeat timeout" (NFR11) with a modest grace factor
  // against ordinary network jitter around the server's own interval timer.
  const STALE_AFTER_INTERVAL_MULTIPLIER = 1.2;

  // A silently UDP-blackholed network never rejects the QUIC handshake --
  // it just never answers -- so `transport.ready` alone cannot be trusted
  // to settle on its own. Without an app-level bound here, "WebTransport
  // unavailable... UDP blocked" (this story's own I/O matrix) would hang
  // the fallback to SSE on exactly the condition it names.
  const WEBTRANSPORT_HANDSHAKE_TIMEOUT_MS = 8000;

  // Reconnect backoff after a dropped leader-tab carrier: starts at the
  // same 50ms a transient drop always used, but grows (capped) instead of
  // retrying forever at a fixed interval -- a sustained failure (expired
  // token, server down) backs off instead of hammering the server.
  const STREAM_RECONNECT_BACKOFF_INITIAL_MS = 50;
  const STREAM_RECONNECT_BACKOFF_MAX_MS = 5000;

  // A newly-arrived `event` cursor is appended here (capped to the last
  // RECEIVED_CURSOR_LOG_LIMIT) -- not needed for the store itself, but it
  // is exactly what NFR13 asks resume-from-cursor be "proven by": a
  // sent-vs-received cursor comparison, which `bridge_spike/check.py`
  // reads via `getReceivedCursorLog()`.
  const RECEIVED_CURSOR_LOG_LIMIT = 500;

  let _streamForceFallback = false;
  const _streamState = {
    carrier: null, // 'webtransport' | 'sse' | null
    cursor: 0,
    heartbeatIntervalSeconds: null,
    lastActivityAt: 0,
    isLeader: false,
    receivedCursors: [],
  };
  let _streamLockController = null;
  let _streamLeaderReleaseResolve = null;

  let _streamBroadcastChannel = null;
  try {
    _streamBroadcastChannel = new BroadcastChannel(STREAM_BROADCAST_CHANNEL_NAME);
  } catch (_err) {
    _streamBroadcastChannel = null; // unsupported: this tab just never hears another tab's stream
  }

  function _storedStreamCursor() {
    try {
      return parseInt(localStorage.getItem(STREAM_CURSOR_STORAGE_KEY) || "0", 10) || 0;
    } catch (_err) {
      return 0;
    }
  }

  function _storeStreamCursor(cursor) {
    _streamState.cursor = Math.max(_streamState.cursor, cursor);
    try {
      localStorage.setItem(STREAM_CURSOR_STORAGE_KEY, String(_streamState.cursor));
    } catch (_err) {
      // best-effort only -- a tab that cannot persist just resumes from 0
    }
  }

  /** Applied whether an envelope arrived over this tab's own carrier (as
   * leader) or was mirrored in from BroadcastChannel (as a follower) --
   * the SAME decision either way, so a follower's view of cursor/staleness
   * never drifts from the leader's. */
  function _applyStreamEnvelope(streamEnvelope, carrier) {
    _streamState.lastActivityAt = performance.now();
    if (streamEnvelope.type === "hello") {
      _streamState.heartbeatIntervalSeconds = streamEnvelope.heartbeat_interval_seconds;
      _storeStreamCursor(streamEnvelope.cursor);
    } else if (streamEnvelope.type === "event") {
      _storeStreamCursor(streamEnvelope.cursor);
      _streamState.receivedCursors.push(streamEnvelope.cursor);
      if (_streamState.receivedCursors.length > RECEIVED_CURSOR_LOG_LIMIT) {
        _streamState.receivedCursors.shift();
      }
    } else if (streamEnvelope.type === "resync") {
      _storeStreamCursor(streamEnvelope.cursor);
    } else if (streamEnvelope.type === "heartbeat") {
      _storeStreamCursor(streamEnvelope.head_cursor);
    }
    if (carrier) {
      _streamState.carrier = carrier;
    }
  }

  function _onLeaderEnvelope(streamEnvelope, carrier) {
    _applyStreamEnvelope(streamEnvelope, carrier);
    if (_streamBroadcastChannel) {
      try {
        _streamBroadcastChannel.postMessage({ kind: "envelope", carrier, envelope: streamEnvelope });
      } catch (_err) {
        // best-effort only
      }
    }
  }

  async function _fetchWebTransportCertHashes() {
    const response = await signedFetch("GET", "/api/stream/cert-hashes");
    if (!response.ok) {
      return null;
    }
    const body = await response.json();
    return (body.hashes || []).map((b64) => ({ algorithm: "sha-256", value: base64ToBuf(b64) }));
  }

  async function _buildWebTransportAuthMessage(cursor) {
    const fields = await buildSignedFields("WEBTRANSPORT", WEBTRANSPORT_PATH, String(cursor));
    return JSON.stringify({ ...fields, cursor });
  }

  /** Reads newline-delimited JSON envelopes off a decoded text stream --
   * the shape both the WebTransport incoming stream and the SSE response
   * body reduce to once decoded, so one reader loop serves both carriers. */
  async function _readEnvelopeLines(textStream, carrier, frameSeparator, stripPrefix) {
    _streamState.carrier = carrier;
    const reader = textStream.getReader();
    let buffer = "";
    try {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        let boundary;
        while ((boundary = buffer.indexOf(frameSeparator)) >= 0) {
          let frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + frameSeparator.length);
          if (stripPrefix && frame.startsWith(stripPrefix)) {
            frame = frame.slice(stripPrefix.length);
          }
          if (!frame) continue;
          try {
            _onLeaderEnvelope(JSON.parse(frame), carrier);
          } catch (err) {
            console.warn("bridge-spike app: malformed stream envelope", err, frame);
          }
        }
      }
    } finally {
      if (_streamState.carrier === carrier) {
        _streamState.carrier = null;
      }
    }
  }

  // Set by whichever carrier is currently live, to a function that force-
  // closes it -- `simulateDrop()` (a test seam, and also what a real
  // backgrounding/app-switch/network-drop looks like from this module's own
  // point of view) calls whatever is currently set. `_runAsStreamLeader`'s
  // reconnect loop below picks the drop straight back up from the stored
  // cursor, same as any other connection loss.
  let _activeDropFn = null;

  /** WebTransport-first connect (NFR14): fetches the signed cert-hash
   * snapshot, tries the handshake, and -- on failure -- refreshes the
   * hashes exactly once and retries before giving up on WebTransport
   * entirely for this connect attempt. Returns `null` (never throws) on
   * any failure so the caller falls back to SSE automatically; returns the
   * live `WebTransport` instance on success. */
  async function _connectWebTransport(cursor) {
    if (_streamForceFallback || !("WebTransport" in window)) {
      return null;
    }
    const attemptHandshake = async () => {
      const hashes = await _fetchWebTransportCertHashes();
      if (!hashes || !hashes.length) {
        throw new Error("no WebTransport certificate hashes available");
      }
      const url = `https://${location.host}${WEBTRANSPORT_PATH}`;
      const transport = new WebTransport(url, { serverCertificateHashes: hashes });
      let timeoutId;
      const timeout = new Promise((_resolve, reject) => {
        timeoutId = setTimeout(
          () => reject(new Error("WebTransport handshake timed out")),
          WEBTRANSPORT_HANDSHAKE_TIMEOUT_MS
        );
      });
      try {
        await Promise.race([transport.ready, timeout]);
      } catch (err) {
        try {
          transport.close();
        } catch (_closeErr) {
          // already closed/closing, or never got far enough to need it
        }
        throw err;
      } finally {
        clearTimeout(timeoutId);
      }
      return transport;
    };

    let transport;
    try {
      transport = await attemptHandshake();
    } catch (err) {
      console.warn("bridge-spike app: WebTransport handshake failed, refreshing cert hashes once", err);
      try {
        transport = await attemptHandshake();
      } catch (err2) {
        console.warn("bridge-spike app: WebTransport handshake failed again, falling back to SSE", err2);
        return null;
      }
    }

    try {
      const authMessage = await _buildWebTransportAuthMessage(cursor);
      const writable = await transport.createUnidirectionalStream();
      const writer = writable.getWriter();
      await writer.write(new TextEncoder().encode(authMessage));
      await writer.close();

      const incomingReader = transport.incomingUnidirectionalStreams.getReader();
      const { value: incomingStream, done } = await incomingReader.read();
      incomingReader.releaseLock();
      if (done || !incomingStream) {
        throw new Error("no incoming WebTransport stream arrived after the auth message");
      }
      _activeDropFn = () => {
        try {
          transport.close();
        } catch (_closeErr) {
          // already closed/closing
        }
      };
      // Fire-and-forget: the read loop runs for the life of the session,
      // feeding _onLeaderEnvelope as lines arrive.
      _readEnvelopeLines(incomingStream.pipeThrough(new TextDecoderStream()), "webtransport", "\n", "").catch(
        (err) => console.warn("bridge-spike app: WebTransport read loop ended", err)
      );
      return transport;
    } catch (err) {
      console.warn("bridge-spike app: WebTransport session setup failed, falling back to SSE", err);
      try {
        transport.close();
      } catch (_closeErr) {
        // already closed/closing
      }
      return null;
    }
  }

  /** `fetch`-streamed SSE GET (never `EventSource` -- NFR14), carrying the
   * same signed headers every other authenticated route uses. Resolves
   * once the stream ends (server close, network drop, `simulateDrop()`, or
   * `stop()`).
   *
   * The signature covers the bare route path with NO query string -- the
   * same convention every other signed route already follows (`path` must
   * be the exact `request.path` the server sees, which excludes the query
   * string) -- so this builds the signed fields against `path` directly
   * and only appends `?cursor=` to the URL actually fetched, rather than
   * going through `signedFetch` (which signs whatever string it is given,
   * verbatim, as both the message AND the URL). */
  async function _connectSse(cursor) {
    const path = "/api/stream/sse";
    const controller = new AbortController();
    _activeDropFn = () => controller.abort();
    let response;
    try {
      const fields = await buildSignedFields("GET", path, "");
      response = await fetch(`${path}?cursor=${encodeURIComponent(String(cursor))}`, {
        method: "GET",
        headers: {
          Authorization: `Bearer ${fields.token}`,
          "X-Bridge-Timestamp": fields.timestamp,
          "X-Bridge-Nonce": fields.nonce,
          "X-Bridge-Signature": fields.signature,
        },
        signal: controller.signal,
      });
    } catch (err) {
      if (controller.signal.aborted) {
        return; // dropped before the response even arrived -- treat like any other end-of-stream
      }
      throw err;
    }
    if (!response.ok || !response.body) {
      throw new Error(`SSE connect failed: ${response.status}`);
    }
    try {
      await _readEnvelopeLines(response.body.pipeThrough(new TextDecoderStream()), "sse", "\n\n", "data: ");
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        throw err;
      }
    }
  }

  /** Holds leadership for as long as this tab keeps it: connects, and on
   * ANY connection end (server close, real network drop, or
   * `simulateDrop()`) reconnects from the stored cursor after a brief
   * backoff -- a transient drop resumes the gap (NFR13) rather than
   * relinquishing the tab's leadership (AD-31: hand-off is on tab close,
   * not on a mere reconnect). Returns only once `releaseLeadership()` is
   * called or this tab's own `navigator.locks` request is aborted. */
  async function _runAsStreamLeader() {
    _streamState.isLeader = true;
    let released = false;
    const releasePromise = new Promise((resolve) => {
      _streamLeaderReleaseResolve = () => {
        released = true;
        resolve();
      };
    });
    let backoffMs = STREAM_RECONNECT_BACKOFF_INITIAL_MS;
    try {
      while (!released) {
        const cursor = _storedStreamCursor();
        _activeDropFn = null;
        let transport = null;
        const attemptStartedAt = Date.now();
        try {
          transport = await _connectWebTransport(cursor);
          const streamEndedPromise = transport
            ? transport.closed.catch(() => {})
            : _connectSse(cursor).catch((err) => {
                console.warn("bridge-spike app: SSE stream ended", err);
              });
          await Promise.race([streamEndedPromise, releasePromise]);
        } finally {
          // `_activeDropFn` closes whichever carrier is actually live --
          // `transport.close()` for WebTransport, `controller.abort()` for
          // SSE (both set it themselves in `_connectWebTransport`/
          // `_connectSse`). Calling `transport.close()` directly here would
          // leak an active SSE fetch, since `transport` is null for that
          // carrier.
          if (_activeDropFn) {
            _activeDropFn();
          }
          _activeDropFn = null;
        }
        if (!released) {
          // A brief backoff before resuming, same shape a real
          // backgrounding/app-switch/network-drop reconnect would use --
          // growing (capped) when attempts fail quickly and back to none
          // once a connection actually holds for a while, so a sustained
          // failure (expired token, server down) backs off instead of
          // hammering the server, while a real transient drop still
          // resumes fast.
          await new Promise((resolve) => setTimeout(resolve, backoffMs));
          const heldConnectionMs = Date.now() - attemptStartedAt;
          backoffMs =
            heldConnectionMs >= STREAM_RECONNECT_BACKOFF_MAX_MS
              ? STREAM_RECONNECT_BACKOFF_INITIAL_MS
              : Math.min(backoffMs * 2, STREAM_RECONNECT_BACKOFF_MAX_MS);
        }
      }
    } finally {
      _streamLeaderReleaseResolve = null;
      _streamState.isLeader = false;
      _streamState.carrier = null;
    }
  }

  /** Begins this tab's leader-election lifecycle. Idempotent per tab: a
   * second call while already connecting/leading/waiting is a no-op. */
  function connect() {
    if (_streamLockController) {
      return;
    }
    _streamLockController = new AbortController();
    if (_streamBroadcastChannel) {
      _streamBroadcastChannel.onmessage = (event) => {
        const message = event.data;
        if (message && message.kind === "envelope" && !_streamState.isLeader) {
          _applyStreamEnvelope(message.envelope, message.carrier);
        }
      };
    }
    navigator.locks
      .request(STREAM_LEADER_LOCK, { signal: _streamLockController.signal }, () => _runAsStreamLeader())
      .catch((err) => {
        if (err && err.name !== "AbortError") {
          console.warn("bridge-spike app: stream leader-election failed", err);
        }
      })
      .finally(() => {
        // The lock request has settled (released, aborted, or failed) --
        // without this, a tab that ever called releaseLeadership() could
        // never connect() again, since `_streamLockController` staying set
        // makes every future connect() call a no-op.
        _streamLockController = null;
      });
  }

  /** Voluntarily gives up leadership so the next waiting tab (in a two-tab
   * scenario) or this same tab's own re-`connect()` can take over. Not
   * currently exercised by `bridge_spike/check.py`, which drives the real
   * "leader tab closes" scenario by literally closing a Playwright page
   * instead -- kept as a same-page-process alternative for a caller that
   * cannot close its own tab. */
  function releaseLeadership() {
    if (_streamLeaderReleaseResolve) {
      _streamLeaderReleaseResolve();
    }
  }

  /** Test-only seam (bridge_spike/check.py) and also this module's own
   * model of "the connection died mid-flow": force-closes whichever
   * carrier is currently live. Leadership is kept -- `_runAsStreamLeader`'s
   * loop reconnects from the stored cursor after a brief backoff, exactly
   * as it would for a real backgrounding/app-switch/network-drop. */
  function simulateDrop() {
    if (_activeDropFn) {
      _activeDropFn();
    }
  }

  /** Every `event` cursor this tab has applied, in the order applied --
   * NFR13's own verification method ("proven by comparing sent vs.
   * received cursors") reads this directly. */
  function getReceivedCursorLog() {
    return _streamState.receivedCursors.slice();
  }

  function getCursor() {
    return _streamState.cursor;
  }

  function getCarrier() {
    return _streamState.carrier;
  }

  function isLeader() {
    return _streamState.isLeader;
  }

  function isStale() {
    if (!_streamState.heartbeatIntervalSeconds || !_streamState.lastActivityAt) {
      return false;
    }
    const staleAfterMs = _streamState.heartbeatIntervalSeconds * 1000 * STALE_AFTER_INTERVAL_MULTIPLIER;
    return performance.now() - _streamState.lastActivityAt > staleAfterMs;
  }

  /** Test-only seam: forces every future `connect()` in this tab to skip
   * WebTransport and go straight to the SSE fallback, deterministically --
   * an automated Chromium check cannot reliably block UDP in-flight to
   * force the real "WebTransport unavailable" branch, mirroring the
   * existing `window.Bridge*` test-seam precedent from Stories 1.2/1.3. */
  function forceFallback(force) {
    _streamForceFallback = Boolean(force);
  }

  window.BridgeStream = {
    connect,
    getCursor,
    getCarrier,
    getReceivedCursorLog,
    isLeader,
    isStale,
    forceFallback,
    releaseLeadership,
    simulateDrop,
  };
})();
