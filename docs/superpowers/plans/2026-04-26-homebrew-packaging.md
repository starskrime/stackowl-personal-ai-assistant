# Homebrew Packaging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship StackOwl as a Homebrew formula so customers install it with `brew tap starskrime/stackowl && brew install stackowl` — no npm, no manual Node.js setup.

**Architecture:** Two new shell scripts in the main repo (`release.sh` auto-bumps patch version + tags; `update-formula.sh` patches formula SHA256s after CI). The Homebrew formula and README live in the separate tap repo `starskrime/homebrew-stackowl` (already created at `https://github.com/starskrime/homebrew-stackowl`). The formula downloads pre-built platform tarballs from GitHub Releases; `depends_on "node@22"` lets Homebrew handle Node.js silently.

**Tech Stack:** Bash, Homebrew Ruby formula DSL, existing GitHub Actions CI

---

## File Structure

| Repo | File | Action |
|------|------|--------|
| `stackowl-personal-ai-assistant` | `scripts/release.sh` | Create |
| `stackowl-personal-ai-assistant` | `scripts/update-formula.sh` | Create |
| `homebrew-stackowl` | `Formula/stackowl.rb` | Create |
| `homebrew-stackowl` | `README.md` | Create |

---

### Task 1: Create `scripts/release.sh`

**Files:**
- Create: `scripts/release.sh`

Context: Run from the root of `stackowl-personal-ai-assistant`. Reads current version from `package.json` using `node -p` (always CJS context, unaffected by `"type": "module"`). Bumps the patch segment, patches `package.json` and `src/index.ts` (which has `.version("0.1.0")` near line 1964), commits, tags, and pushes — triggering GitHub Actions.

- [ ] **Step 1: Verify the version string location in `src/index.ts`**

```bash
grep -n '\.version(' src/index.ts
```
Expected: a line like `1964:  .version("0.1.0");` — confirms the sed pattern will match.

- [ ] **Step 2: Create `scripts/release.sh`**

```bash
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
```

- [ ] **Step 3: Make executable**

```bash
chmod +x scripts/release.sh
```

- [ ] **Step 4: Syntax check**

```bash
bash -n scripts/release.sh
```
Expected: no output (clean syntax).

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh
git commit -m "feat(scripts): add release.sh for auto patch version bump"
```

---

### Task 2: Create `scripts/update-formula.sh`

**Files:**
- Create: `scripts/update-formula.sh`

Context: Run after GitHub Actions finishes building tarballs. Streams each tarball through `curl | shasum` (no temp files). Uses `sed` with inline comment anchors (`# darwin-arm64` etc.) to patch each `sha256` line in the formula unambiguously. The `-f` flag on `curl` causes it to fail fast with a non-zero exit on HTTP errors (e.g. 404 if the release doesn't exist yet).

- [ ] **Step 1: Create `scripts/update-formula.sh`**

```bash
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
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/update-formula.sh
```

- [ ] **Step 3: Syntax check**

```bash
bash -n scripts/update-formula.sh
```
Expected: no output (clean syntax).

- [ ] **Step 4: Commit**

```bash
git add scripts/update-formula.sh
git commit -m "feat(scripts): add update-formula.sh for homebrew SHA256 patching"
```

---

### Task 3: Create `Formula/stackowl.rb` in the tap repo

**Files:**
- Create: `../homebrew-stackowl/Formula/stackowl.rb`

Context: Work in the `homebrew-stackowl` repo (separate from the main repo). Clone it to `../homebrew-stackowl` (sibling directory). SHA256 values are set to 64-char zero strings — valid placeholders; `update-formula.sh` replaces them after the first real release. The `inreplace` in `def install` hardcodes `#{prefix}` as `STACKOWL_ROOT` because Homebrew symlinks `bin/stackowl` from `$(brew --prefix)/bin/`, which breaks the launcher's relative `$(dirname "$0")/..` path.

- [ ] **Step 1: Clone the tap repo (run from parent of `stackowl-personal-ai-assistants`)**

```bash
cd /Users/bakirtalibov/Desktop
git clone https://github.com/starskrime/homebrew-stackowl.git
cd homebrew-stackowl
```

- [ ] **Step 2: Create the Formula directory**

```bash
mkdir -p Formula
```

- [ ] **Step 3: Create `Formula/stackowl.rb`**

```ruby
class Stackowl < Formula
  desc "Personal AI assistant with multi-owl personalities and Parliament brainstorming"
  homepage "https://github.com/starskrime/stackowl-personal-ai-assistant"
  version "0.1.0"

  depends_on "node@22"

  on_macos do
    on_arm do
      url "https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/v#{version}/stackowl-v#{version}-darwin-arm64.tar.gz"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000" # darwin-arm64
    end
    on_intel do
      url "https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/v#{version}/stackowl-v#{version}-darwin-x86_64.tar.gz"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000" # darwin-x86_64
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/v#{version}/stackowl-v#{version}-linux-x86_64.tar.gz"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000" # linux-x86_64
    end
  end

  def install
    lib.install "lib/dist"
    lib.install "lib/node_modules"
    bin.install "bin/stackowl"
    inreplace bin/"stackowl",
      'STACKOWL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"',
      "STACKOWL_ROOT=#{prefix}"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/stackowl --version 2>&1")
  end
end
```

- [ ] **Step 4: Verify Ruby syntax**

```bash
ruby -c Formula/stackowl.rb
```
Expected: `Syntax OK`

- [ ] **Step 5: Run `brew audit` if Homebrew is available**

```bash
brew audit --formula Formula/stackowl.rb
```
Expected: no errors. Warnings about missing `license` or `head` are acceptable.

- [ ] **Step 6: Commit and push**

```bash
git add Formula/stackowl.rb
git commit -m "feat: add stackowl homebrew formula"
git push
```

---

### Task 4: Create `README.md` in the tap repo

**Files:**
- Create: `../homebrew-stackowl/README.md`

Context: Still in `../homebrew-stackowl`. This is what customers see on the GitHub tap repo page.

- [ ] **Step 1: Create `README.md`**

```markdown
# homebrew-stackowl

Homebrew tap for [StackOwl](https://github.com/starskrime/stackowl-personal-ai-assistant) — a personal AI assistant with multi-owl personalities, Parliament brainstorming, and more.

## Install

```bash
brew tap starskrime/stackowl
brew install stackowl
```

## First run

```bash
stackowl start
```

## Upgrade

```bash
brew upgrade stackowl
```

## Requirements

- macOS (Apple Silicon or Intel) or Linux x86_64
- Node.js 22 — installed automatically by Homebrew
```

- [ ] **Step 2: Commit and push**

```bash
git add README.md
git commit -m "docs: add install instructions"
git push
```

---

### Task 5: Smoke test the tap

Context: Verify the tap is discoverable and the formula is parseable by Homebrew before doing any real release.

- [ ] **Step 1: Tap from GitHub**

```bash
brew tap starskrime/stackowl
```
Expected: Homebrew clones `https://github.com/starskrime/homebrew-stackowl` and registers the tap with no errors.

- [ ] **Step 2: Confirm formula is found**

```bash
brew info stackowl
```
Expected: prints the formula description (`Personal AI assistant...`), version `0.1.0`, and homepage. May say "Not installed" — that is fine.

- [ ] **Step 3: Untap (cleanup)**

```bash
brew untap starskrime/stackowl
```

---

## Self-Review

**Spec coverage:**
- `scripts/release.sh` (auto patch bump, commit, tag, push) → Task 1 ✓
- `scripts/update-formula.sh` (SHA256 patching via `sed` + comment anchors) → Task 2 ✓
- `Formula/stackowl.rb` (platform bottles, `depends_on "node@22"`, `inreplace`) → Task 3 ✓
- `README.md` in tap repo → Task 4 ✓
- Smoke test tap discoverability → Task 5 ✓
- No CI changes, no `build-release.sh` changes → confirmed ✓

**Placeholder scan:** SHA256 values in the formula are explicit 64-char zero strings, not vague "TBD". `update-formula.sh` replaces them on first real release. ✓

**Type consistency:** No cross-task type dependencies (pure shell + Ruby DSL). ✓
