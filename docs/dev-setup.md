# StackOwl Dev Setup

## Prerequisites

- Node.js ≥ 22
- npm 10+
- Git
- (Optional) Docker, for the sandboxed `code-sandbox` tool

## First-time setup

```bash
git clone <repo-url>
cd stackowl-personal-ai-assistant
npm install
```

### Jetson / ARM Linux note

On NVIDIA Jetson and some ARM Linux distros, `npm install` can leave `node_modules`
in a state where symlinks within the tree exceed the kernel's link-depth limit.
If you see `Too many levels of symbolic links` errors when running `npm test` or
`tsx`, run:

```bash
sudo npm install
```

once. Subsequent installs do not need sudo.

### Puppeteer note

The `puppeteer` dependency tries to download Chrome at install time. On ARM Linux
this is skipped — install Chromium manually:

```bash
sudo apt install chromium
```

The `live_browser` and `web_fetch` tools detect Chromium via the system PATH.

### Docker note

The `code_sandbox` tool isolates user code in a Docker container when one is
available, with a host fallback (degraded — no isolation) when not. To get full
isolation:

1. Install Docker following the OS-specific guide:
   - macOS: https://docs.docker.com/desktop/install/mac-install/
   - Linux: https://docs.docker.com/engine/install/
   - Windows: https://docs.docker.com/desktop/install/windows-install/

2. Pull the two sandbox images once. This makes the first `code_sandbox` call fast:

```bash
docker pull python:3.12-slim
docker pull node:22-alpine
```

If the images aren't pulled, the tool returns `E_IMAGE_NOT_PULLED` with the
exact `docker pull` command to run rather than blocking 30+ seconds on a
download.

## Running

| Command | What it does |
|---|---|
| `npm run dev` | Run in watch mode (tsx watch) — TUI v2 default |
| `STACKOWL_TUI=v1 npm run dev` | Use the legacy TUI v1 |
| `STACKOWL_JSON=true npx tsx src/index.ts chat` | Non-TTY chat — no Ink renderer |
| `npm run build` | Compile TypeScript to `dist/` |
| `npm start` | Run compiled output |
| `npm test` | Run all tests (vitest) |
| `npm run test:platform` | Platform-layer tests only |
| `npm run lint` | ESLint on `src/` |

## Platform tests

The platform layer at `src/platform/` is tested independently:

```bash
npm run test:platform
```

These tests run against the host OS. To exercise all three OS branches (macOS,
Linux, Windows), CI runs the same suite on `ubuntu-latest`, `macos-latest`, and
`windows-latest`. Local runs only exercise the host OS — platform-specific
branches in the impls are covered by stubbed-platform unit tests where possible.

## Environment variables

| Var | Purpose |
|---|---|
| `STACKOWL_TUI` | `v1` to use legacy TUI; default is v2 |
| `STACKOWL_JSON` | `true` to emit JSON-mode output for non-TTY contexts |
| `IN_DOCKER` | `true` to signal containerized execution (also auto-detected via `/.dockerenv`) |

## Configuration

`stackowl.config.json` is the per-machine config (provider keys, channel tokens,
parliament settings). It is **gitignored** and must be created via `./start.sh`
on first run.
