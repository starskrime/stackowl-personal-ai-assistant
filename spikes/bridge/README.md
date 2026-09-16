# Bridge TLS/mDNS spike kit

**This is a throwaway kit.** It exists to prove, on real hardware, that a
per-install ephemeral CA plus a single `.local` host name can serve trusted
HTTPS on a home network — the Bridge's TLS/origin strategy (AD-13, AD-14).
It is not platform code: nothing under `spikes/bridge/` is imported by
`src/`, and its dependencies are declared inline in `kit.py` via a
[PEP 723](https://peps.python.org/pep-0723/) `# /// script` block. They are
**never** added to the repo's `pyproject.toml` or `uv.lock`.

## Run it

```bash
uv run spikes/bridge/kit.py start
```

`uv run` reads the dependency block at the top of `kit.py` and builds (and
caches) an isolated environment for this script alone — no project lockfile
is touched. This prints the new CA's SHA-256 fingerprint and guided trust
steps, advertises the install name over mDNS, and serves the kit's PWA at
`https://<install-name>:<port>/` until you press Ctrl-C.

Other subcommands:

```bash
uv run spikes/bridge/kit.py renew   # new CA + cert, prints the re-trust ceremony
uv run spikes/bridge/kit.py check   # the kit's own automated done-check (Chromium)
```

Both accept `--name <install-name>` and `--port <port>` (default `8443`).

## What "done" means for this story

Real-device trust ceremonies (iOS, Android, desktop Chrome — by hand, on a
phone) are **Story 1.6's** job, not this one. This story (1.1) is done when
`uv run spikes/bridge/kit.py check` passes against Chromium **on the build
host**: it proves the certificate chain, the redirect, the subnet refusal,
and result writing, and writes `results/B1-build-host-chromium.json`.

## Layout

- `kit.py` — the single entrypoint (`start` / `renew` / `check`).
- `bridge_spike/ca.py` — ephemeral CA + one signed leaf cert; the CA private
  key is never written to disk and is dropped from memory the moment the
  leaf is signed.
- `bridge_spike/mdns.py` — advertises `<install-name>.local` via
  `avahi-publish-service` (Linux) or `dns-sd -R` (macOS); prints the exact
  command instead of crashing if the tool is missing.
- `bridge_spike/server.py` — the one HTTPS listener: IPv4-only, subnet
  allowlist, redirect-first middleware, the static PWA, and the checklist
  results endpoint.
- `bridge_spike/check.py` — the automated Chromium check shared by
  `kit.py check` and `tests/test_automated_check.py`.
- `bridge_spike/static/` — the PWA (`index.html`, `manifest.webmanifest`,
  `sw.js`, icons).
- `tests/` — `test_ca.py`, `test_server.py`, `test_automated_check.py`.
- `results/` — checklist + check output, gitignored (`.gitkeep` keeps the
  directory itself versioned).

## Tests

```bash
# CA + server unit tests (no Playwright needed):
uv run --with cryptography --with aiohttp python -m pytest spikes/bridge/tests

# The full automated done-check, including the Playwright/Chromium test:
uv run spikes/bridge/kit.py check
```

## Never

- Touch the platform's own TLS/secret-store code — this kit is fully
  self-contained.
- Add a WireGuard/tunnel path — that approach (AD-15) is retired.
- Relax the redirect or subnet checks "to make a demo easier".
- Commit generated CA/server private keys or `results/*.json`.
