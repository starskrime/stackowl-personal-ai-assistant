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

  /** Every authenticated request: bearer token + a signature over
   * method/path/timestamp/server-nonce/body-hash, signed by the
   * non-extractable device key. `path` must be the exact request path
   * (no origin, no query string) -- the server signs over the same thing
   * it received on `request.path`. */
  async function signedFetch(method, path, bodyObj) {
    const device = await getStoredDevice();
    if (!device) {
      throw new Error("not signed in: no device key/token in IndexedDB");
    }
    const nonce = await fetchNonce();
    const timestamp = String(Math.floor(Date.now() / 1000));
    const bodyText = bodyObj !== undefined ? JSON.stringify(bodyObj) : "";
    const bodyHash = await sha256Hex(new TextEncoder().encode(bodyText));
    const message = `${method}\n${path}\n${timestamp}\n${nonce}\n${bodyHash}`;
    const signature = await crypto.subtle.sign(
      { name: "ECDSA", hash: "SHA-256" },
      device.privateKey,
      new TextEncoder().encode(message)
    );
    const headers = {
      Authorization: `Bearer ${device.token}`,
      "X-Bridge-Timestamp": timestamp,
      "X-Bridge-Nonce": nonce,
      "X-Bridge-Signature": bufToBase64(signature),
    };
    const init = { method, headers };
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
})();
