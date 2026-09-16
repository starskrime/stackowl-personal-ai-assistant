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
  };
})();
