#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?Usage: update-formula.sh <version> [tap-dir]}"
TAP_DIR="${2:-../homebrew-stackowl}"
FORMULA="$TAP_DIR/Formula/stackowl.rb"
BASE="https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/${VERSION}"
VER="${VERSION#v}"

if [[ ! -f "$FORMULA" ]]; then
  echo "Error: Formula not found at $FORMULA" >&2
  echo "Make sure homebrew-stackowl is cloned at $TAP_DIR" >&2
  exit 1
fi

echo "Fetching SHA256s for ${VERSION} (streams all 3 tarballs)..."

SHA_ARM64=$(curl -fSL "${BASE}/stackowl-${VERSION}-darwin-arm64.tar.gz"  | shasum -a 256 | awk '{print $1}')
SHA_X86=$(  curl -fSL "${BASE}/stackowl-${VERSION}-darwin-x86_64.tar.gz" | shasum -a 256 | awk '{print $1}')
SHA_LINUX=$( curl -fSL "${BASE}/stackowl-${VERSION}-linux-x86_64.tar.gz"  | shasum -a 256 | awk '{print $1}')

echo "  darwin-arm64:  $SHA_ARM64"
echo "  darwin-x86_64: $SHA_X86"
echo "  linux-x86_64:  $SHA_LINUX"

sed -i.bak "s/version \".*\"/version \"${VER}\"/"                                                         "$FORMULA"
sed -i.bak "s|sha256 \".*\" # darwin-arm64|sha256 \"${SHA_ARM64}\" # darwin-arm64|"                      "$FORMULA"
sed -i.bak "s|sha256 \".*\" # darwin-x86_64|sha256 \"${SHA_X86}\" # darwin-x86_64|"                     "$FORMULA"
sed -i.bak "s|sha256 \".*\" # linux-x86_64|sha256 \"${SHA_LINUX}\" # linux-x86_64|"                     "$FORMULA"
rm -f "${FORMULA}.bak"

echo ""
echo "✓ Patched $FORMULA"
echo ""
echo "Next steps:"
echo "  cd $TAP_DIR"
echo "  git diff Formula/stackowl.rb"
echo "  git commit -am 'stackowl ${VER}' && git push"
