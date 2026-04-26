#!/usr/bin/env bash
set -euo pipefail

CURRENT=$(node -p "require('./package.json').version")
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"
NEW_PATCH=$(( PATCH + 1 ))
NEW_VERSION="${MAJOR}.${MINOR}.${NEW_PATCH}"
TAG="v${NEW_VERSION}"

echo "Bumping ${CURRENT} → ${NEW_VERSION}"

sed -i.bak "s/\"version\": \"${CURRENT}\"/\"version\": \"${NEW_VERSION}\"/" package.json && rm -f package.json.bak
sed -i.bak "s/\.version(\"${CURRENT}\")/\.version(\"${NEW_VERSION}\")/" src/index.ts && rm -f src/index.ts.bak

git add package.json src/index.ts
git commit -m "chore(release): ${TAG}"
git tag "${TAG}"
git push && git push --tags

echo ""
echo "✓ Tagged ${TAG} — GitHub Actions is building tarballs now (~10 min)"
echo ""
echo "After CI finishes, run:"
echo "  ./scripts/update-formula.sh ${TAG} ../homebrew-stackowl"
