// Story 1.5 (AD-36): externalized verbatim (same DOM ids, same window.Bridge*
// calls) from index.html's inline <script> -- the enforced `script-src
// 'self'` has no 'unsafe-inline', so every script must be its own
// same-origin file. app.js (loaded first, via its own <script src>) is
// unaffected -- it was already external.
//
// `ServiceWorkerContainer.register()`'s URL argument is a TrustedScriptURL
// sink: under the now-global `require-trusted-types-for 'script'`, passing
// a raw string throws (silently, if the .catch() below doesn't log it) and
// registration never happens at all -- this is a legacy-page-only policy,
// entirely separate from frontend/src/'s one named policy (NFR22's "kit's
// front-end code" scope for the Svelte/Three.js build check.py verifies);
// this page is a distinct pre-existing document from Stories 1.1-1.4 that
// never shares a JS realm with /csp-check/, so it needs its own.
const legacySwRegisterPolicy =
  window.trustedTypes && window.trustedTypes.createPolicy
    ? window.trustedTypes.createPolicy("bridge-spike-legacy-sw-register", {
        createScriptURL: (url) => url,
      })
    : null;

if ("serviceWorker" in navigator) {
  const swScriptUrl = legacySwRegisterPolicy ? legacySwRegisterPolicy.createScriptURL("/sw.js") : "/sw.js";
  navigator.serviceWorker.register(swScriptUrl).catch((err) => {
    console.error("bridge-spike index-bootstrap: service worker registration failed", err);
  });
}

(async () => {
  const statusEl = document.getElementById("signin-status");
  const signedIn = await window.BridgeAuth.isSignedIn();
  statusEl.textContent = signedIn ? "Signed in on this device." : "Not signed in on this device.";
})().catch((err) => {
  document.getElementById("signin-status").textContent = `Could not check sign-in state: ${err}`;
});

document.getElementById("register-passkey-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("passkey-result");
  const setupCode = document.getElementById("setup-code-input").value.trim();
  resultEl.textContent = "Creating passkey…";
  try {
    const result = await window.BridgeAuth.registerPasskey(setupCode);
    resultEl.textContent = result.ok ? "Passkey created and signed in." : `Failed at ${result.step}: ${result.status}`;
    resultEl.className = result.ok ? "ok" : "error";
  } catch (err) {
    resultEl.textContent = `Error: ${err}`;
    resultEl.className = "error";
  }
});

document.getElementById("authenticate-passkey-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("passkey-result");
  resultEl.textContent = "Signing in…";
  try {
    const result = await window.BridgeAuth.authenticatePasskey();
    resultEl.textContent = result.ok ? "Signed in." : `Failed at ${result.step}: ${result.status}`;
    resultEl.className = result.ok ? "ok" : "error";
  } catch (err) {
    resultEl.textContent = `Error: ${err}`;
    resultEl.className = "error";
  }
});

document.getElementById("request-device-access-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("device-request-result");
  const deviceName = document.getElementById("device-name-input").value.trim();
  resultEl.textContent = "Requesting access…";
  try {
    const result = await window.BridgeAuth.requestDeviceAccess(deviceName);
    if (!result.ok) {
      resultEl.textContent = `Rejected: ${result.status}`;
      resultEl.className = "error";
      return;
    }
    resultEl.textContent = `Matching code: ${result.code} — waiting for approval…`;
    resultEl.className = "";
    const poll = async () => {
      const status = await window.BridgeAuth.pollDeviceRequestStatus(result.request_id);
      if (!status.ok) {
        // Terminal: e.g. the request expired and now 404s. Retrying
        // forever would never recover -- the owner has to start over.
        resultEl.textContent = "Request expired — start again.";
        resultEl.className = "error";
        return;
      }
      if (status.approved) {
        resultEl.textContent = "Approved — signed in on this device.";
        resultEl.className = "ok";
        return;
      }
      setTimeout(poll, 2000);
    };
    poll();
  } catch (err) {
    resultEl.textContent = `Error: ${err}`;
    resultEl.className = "error";
  }
});

document.getElementById("refresh-pending-btn").addEventListener("click", async () => {
  const pendingEl = document.getElementById("pending-request");
  pendingEl.textContent = "Checking…";
  try {
    const result = await window.BridgeAuth.getPendingDeviceRequest();
    if (!result.ok) {
      pendingEl.textContent = `Error: ${result.status}`;
      return;
    }
    if (!result.pending) {
      pendingEl.textContent = "No device request is pending.";
      return;
    }
    const { request_id, device_name, code } = result.pending;
    pendingEl.replaceChildren();
    const label = document.createElement("p");
    label.textContent = `${device_name} — code: ${code}`;
    const approveBtn = document.createElement("button");
    approveBtn.type = "button";
    approveBtn.textContent = "Approve";
    approveBtn.addEventListener("click", async () => {
      try {
        const approval = await window.BridgeAuth.approveDeviceRequest(request_id, code);
        pendingEl.textContent = approval.ok ? "Approved." : `Approval failed: ${approval.status}`;
      } catch (err) {
        pendingEl.textContent = `Error: ${err}`;
      }
    });
    pendingEl.appendChild(label);
    pendingEl.appendChild(approveBtn);
  } catch (err) {
    pendingEl.textContent = `Error: ${err}`;
  }
});

document.getElementById("enable-push-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("push-result");
  resultEl.textContent = "Requesting notification permission…";
  try {
    if ("Notification" in window && Notification.permission !== "granted") {
      await Notification.requestPermission();
    }
    const result = await window.BridgePush.subscribe();
    resultEl.textContent = result.ok
      ? `Subscribed: ${result.endpoint}`
      : `Failed at ${result.step}: ${result.status}`;
    resultEl.className = result.ok ? "ok" : "error";
  } catch (err) {
    resultEl.textContent = `Error: ${err}`;
    resultEl.className = "error";
  }
});

document.getElementById("start-mic-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("mic-result");
  const meterEl = document.getElementById("mic-meter");
  resultEl.textContent = "Requesting microphone access…";
  try {
    const result = await window.BridgeMic.captureLevel();
    meterEl.value = result.level || 0;
    const permission = await window.BridgeMic.permissionState();
    resultEl.textContent = result.ok
      ? `Captured audio — peak level ${(result.level * 100).toFixed(0)}%. Permission: ${permission}.`
      : `Failed: ${result.error || "unknown error"}`;
    resultEl.className = result.ok ? "ok" : "error";
  } catch (err) {
    resultEl.textContent = `Error: ${err}`;
    resultEl.className = "error";
  }
});

document.getElementById("start-stream-btn").addEventListener("click", () => {
  window.BridgeStream.connect();
});

document.getElementById("force-fallback-btn").addEventListener("click", (event) => {
  window.BridgeStream.forceFallback(true);
  event.target.textContent = "SSE fallback forced";
});

setInterval(() => {
  const carrier = window.BridgeStream.getCarrier();
  document.getElementById("stream-carrier").textContent = carrier || "—";
  document.getElementById("stream-cursor").textContent = window.BridgeStream.getCursor();
  const statusEl = document.getElementById("stream-status");
  if (!carrier) {
    statusEl.textContent = "not connected";
    statusEl.className = "";
  } else if (window.BridgeStream.isStale()) {
    statusEl.textContent = "stale — no heartbeat within the announced interval";
    statusEl.className = "error";
  } else {
    statusEl.textContent = window.BridgeStream.isLeader() ? "live (this tab is the leader)" : "live (mirrored)";
    statusEl.className = "ok";
  }
}, 500);

const DEVICE_CLASS_RE = /^[A-Za-z0-9_-]{1,64}$/;

document.getElementById("checklist-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const statusEl = document.getElementById("status");
  const deviceClass = document.getElementById("device_class").value.trim();
  const note = document.getElementById("note").value.trim();
  const passed = document.getElementById("passed").value === "true";

  if (!DEVICE_CLASS_RE.test(deviceClass)) {
    statusEl.textContent = "Rejected: device class is malformed.";
    statusEl.className = "error";
    return;
  }
  if (!note) {
    statusEl.textContent = "Rejected: a note is required.";
    statusEl.className = "error";
    return;
  }

  try {
    const response = await fetch("/api/results", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        device_class: deviceClass,
        passed: passed,
        note: note,
        browser: navigator.userAgent,
        os: navigator.platform,
      }),
    });
    if (!response.ok) {
      const body = await response.text();
      statusEl.textContent = `Rejected by server: ${body}`;
      statusEl.className = "error";
      return;
    }
    const body = await response.json();
    statusEl.textContent = `Recorded: ${body.path}`;
    statusEl.className = "ok";
  } catch (err) {
    statusEl.textContent = `Network error: ${err}`;
    statusEl.className = "error";
  }
});
