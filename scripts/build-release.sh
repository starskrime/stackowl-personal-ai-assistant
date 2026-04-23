#!/usr/bin/env bash
# build-release.sh — Create a platform-specific StackOwl release tarball.
#
# Usage:
#   bash scripts/build-release.sh v0.1.0 darwin-arm64
#
# Output:
#   stackowl-v0.1.0-darwin-arm64.tar.gz
#
# The tarball contains everything needed to run StackOwl:
#   bin/stackowl          — launcher shell script
#   lib/dist/             — compiled TypeScript
#   lib/node_modules/     — production dependencies (platform-native addons included)
#
# It does NOT contain:
#   stackowl.config.json  — lives in ~/.stackowl/ (created by /onboarding on first run)
#   workspace/            — user data, lives in ~/.stackowl/workspace/
#   session.tmp           — runtime state, never packaged

set -euo pipefail

VERSION="${1:?Usage: build-release.sh <version> <target>}"
TARGET="${2:?Usage: build-release.sh <version> <target>}"
PKG="stackowl-${VERSION}-${TARGET}"

echo "Building ${PKG}..."

# ── Sanity checks ─────────────────────────────────────────────────
if [[ ! -d dist ]]; then
  echo "Error: dist/ not found. Run 'npm run build' first." >&2
  exit 1
fi
if [[ ! -d node_modules ]]; then
  echo "Error: node_modules/ not found. Run 'npm ci --omit=dev' first." >&2
  exit 1
fi

# ── Assemble package ──────────────────────────────────────────────
rm -rf "$PKG"
mkdir -p "$PKG/bin" "$PKG/lib"

# Compiled JS + bundled defaults (owls/defaults and skills/defaults
# are already copied into dist/ by the build script)
cp -r dist "$PKG/lib/dist"

# Production node_modules (includes platform-compiled native addons)
cp -r node_modules "$PKG/lib/node_modules"

# Launcher script — resolves its own location so it works from any $PATH
cat > "$PKG/bin/stackowl" << 'LAUNCHER'
#!/bin/sh
STACKOWL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec node "$STACKOWL_ROOT/lib/dist/index.js" "$@"
LAUNCHER
chmod +x "$PKG/bin/stackowl"

# ── Package ───────────────────────────────────────────────────────
ARCHIVE="${PKG}.tar.gz"
tar -czf "$ARCHIVE" "$PKG"
rm -rf "$PKG"

# Print checksum (used to update the Homebrew formula)
if command -v shasum &>/dev/null; then
  CHECKSUM=$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')
else
  CHECKSUM=$(sha256sum "$ARCHIVE" | awk '{print $1}')
fi

echo ""
echo "Created: ${ARCHIVE}"
echo "SHA256:  ${CHECKSUM}"
echo ""
echo "Update Formula/stackowl.rb with this sha256 for the ${TARGET} bottle."
