#!/usr/bin/env bash
# build-release.sh — Create a platform-specific StackOwl release archive.
#
# Usage:
#   bash scripts/build-release.sh v0.1.0 darwin-arm64
#   bash scripts/build-release.sh v0.1.0 linux-x86_64
#   bash scripts/build-release.sh v0.1.0 windows-x86_64
#
# Output:
#   stackowl-v0.1.0-darwin-arm64.tar.gz   (macOS/Linux)
#   stackowl-v0.1.0-windows-x86_64.zip    (Windows)

set -euo pipefail

VERSION="${1:?Usage: build-release.sh <version> <target>}"
TARGET="${2:?Usage: build-release.sh <version> <target>}"
PKG="stackowl-${VERSION}-${TARGET}"

echo "Building ${PKG}..."

if [[ ! -d dist ]]; then
  echo "Error: dist/ not found. Run 'npm run build' first." >&2
  exit 1
fi
if [[ ! -d node_modules ]]; then
  echo "Error: node_modules/ not found. Run 'npm prune --omit=dev' first." >&2
  exit 1
fi

rm -rf "$PKG"
mkdir -p "$PKG/bin" "$PKG/lib"

cp -r dist "$PKG/lib/dist"
cp -r node_modules "$PKG/lib/node_modules"

if [[ "$TARGET" == windows-* ]]; then
  # Windows: .cmd launcher + .zip archive
  cat > "$PKG/bin/stackowl.cmd" << 'LAUNCHER'
@echo off
set STACKOWL_ROOT=%~dp0..
node "%STACKOWL_ROOT%\lib\dist\index.js" %*
LAUNCHER

  ARCHIVE="${PKG}.zip"
  if command -v zip &>/dev/null; then
    zip -r "$ARCHIVE" "$PKG"
  else
    powershell -Command "Compress-Archive -Path '${PKG}' -DestinationPath '${ARCHIVE}'"
  fi
else
  # macOS / Linux: shell launcher + .tar.gz archive
  cat > "$PKG/bin/stackowl" << 'LAUNCHER'
#!/bin/sh
STACKOWL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec node "$STACKOWL_ROOT/lib/dist/index.js" "$@"
LAUNCHER
  chmod +x "$PKG/bin/stackowl"

  ARCHIVE="${PKG}.tar.gz"
  tar -czf "$ARCHIVE" "$PKG"
fi

rm -rf "$PKG"

if command -v shasum &>/dev/null; then
  CHECKSUM=$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')
else
  CHECKSUM=$(sha256sum "$ARCHIVE" | awk '{print $1}')
fi

echo ""
echo "Created: ${ARCHIVE}"
echo "SHA256:  ${CHECKSUM}"
