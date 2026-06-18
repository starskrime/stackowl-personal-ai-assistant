#!/usr/bin/env bash
set -euo pipefail

TAP_DIR="${1:-../homebrew-stackowl}"
VERSION=$(git describe --tags --abbrev=0)
VER="${VERSION#v}"

echo "Pushing ${VERSION} to Homebrew tap at ${TAP_DIR}..."

./scripts/update-formula.sh "${VERSION}" "${TAP_DIR}"

cd "${TAP_DIR}"
git diff Formula/stackowl.rb
git commit -am "stackowl ${VER}"
git push

echo ""
echo "✓ Homebrew formula updated to ${VERSION}"
echo "  Customers run: brew upgrade stackowl"
