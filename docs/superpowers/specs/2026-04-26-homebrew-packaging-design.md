# Homebrew Packaging — Design Spec

**Goal:** Package StackOwl as a Homebrew formula so customers can install it with `brew tap starskrime/stackowl && brew install stackowl` — no npm, no Node.js manual setup, no source checkout required.

**Architecture:** A custom Homebrew tap repo (`starskrime/homebrew-stackowl`) hosts `Formula/stackowl.rb`. The formula downloads platform-specific pre-built tarballs from GitHub Releases. Each tarball bundles `dist/` + `node_modules/`, so npm is never needed. Homebrew auto-installs `node@22` as a dependency. A local helper script (`scripts/update-formula.sh`) computes SHA256s and patches the formula on each release.

**Tech Stack:** Homebrew Ruby formula DSL, Bash (update script), existing GitHub Actions CI

---

## Repository Structure

### Existing repo: `starskrime/stackowl-personal-ai-assistant`

Add one file:
```
scripts/
  build-release.sh       (existing)
  update-formula.sh      NEW — patches formula SHA256s and version on release
```

### New repo: `starskrime/homebrew-stackowl`

Create as a public GitHub repo (empty init):
```
Formula/
  stackowl.rb            Homebrew formula
README.md                Customer install instructions
```

---

## Formula: `Formula/stackowl.rb`

```ruby
class Stackowl < Formula
  desc "Personal AI assistant with multi-owl personalities and Parliament brainstorming"
  homepage "https://github.com/starskrime/stackowl-personal-ai-assistant"
  version "0.1.0"

  depends_on "node@22"

  on_macos do
    on_arm do
      url "https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/v#{version}/stackowl-v#{version}-darwin-arm64.tar.gz"
      sha256 "PLACEHOLDER_ARM64" # darwin-arm64
    end
    on_intel do
      url "https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/v#{version}/stackowl-v#{version}-darwin-x86_64.tar.gz"
      sha256 "PLACEHOLDER_X86_64" # darwin-x86_64
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/v#{version}/stackowl-v#{version}-linux-x86_64.tar.gz"
      sha256 "PLACEHOLDER_LINUX" # linux-x86_64
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

**Key design decisions:**

- `depends_on "node@22"` — Homebrew silently installs Node.js if not present; customer never runs `node` manually
- `inreplace` patches the launcher's `STACKOWL_ROOT` to the Cellar path at install time — required because Homebrew symlinks `bin/stackowl` from `$(brew --prefix)/bin/`, which breaks the relative `$(dirname "$0")/..` path in the launcher
- Inline `# darwin-arm64` / `# darwin-x86_64` / `# linux-x86_64` comments on each `sha256` line act as stable anchors for `sed` in the update script
- `test do` block runs `stackowl --version` and asserts the version string is present — required for `brew test stackowl` to pass

---

## Update Script: `scripts/update-formula.sh`

Run locally after GitHub Actions finishes building a release.

**Usage:**
```bash
./scripts/update-formula.sh v0.2.0 ../homebrew-stackowl
```

**Script:**
```bash
#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?Usage: update-formula.sh <version> [tap-dir]}"
TAP_DIR="${2:-../homebrew-stackowl}"
FORMULA="$TAP_DIR/Formula/stackowl.rb"
BASE="https://github.com/starskrime/stackowl-personal-ai-assistant/releases/download/${VERSION}"
VER="${VERSION#v}"

echo "Fetching SHA256s for ${VERSION} (streams all 3 tarballs — no temp files)..."

SHA_ARM64=$(curl -sL "${BASE}/stackowl-${VERSION}-darwin-arm64.tar.gz"  | shasum -a 256 | awk '{print $1}')
SHA_X86=$(  curl -sL "${BASE}/stackowl-${VERSION}-darwin-x86_64.tar.gz" | shasum -a 256 | awk '{print $1}')
SHA_LINUX=$( curl -sL "${BASE}/stackowl-${VERSION}-linux-x86_64.tar.gz"  | shasum -a 256 | awk '{print $1}')

echo "  darwin-arm64:  $SHA_ARM64"
echo "  darwin-x86_64: $SHA_X86"
echo "  linux-x86_64:  $SHA_LINUX"

sed -i.bak "s/version \".*\"/version \"${VER}\"/"                                                          "$FORMULA"
sed -i.bak "s|sha256 \".*\" # darwin-arm64|sha256 \"${SHA_ARM64}\" # darwin-arm64|"                       "$FORMULA"
sed -i.bak "s|sha256 \".*\" # darwin-x86_64|sha256 \"${SHA_X86}\" # darwin-x86_64|"                      "$FORMULA"
sed -i.bak "s|sha256 \".*\" # linux-x86_64|sha256 \"${SHA_LINUX}\" # linux-x86_64|"                      "$FORMULA"

rm -f "${FORMULA}.bak"

echo ""
echo "✓ Patched $FORMULA"
echo ""
echo "Next steps:"
echo "  cd $TAP_DIR"
echo "  git diff Formula/stackowl.rb"
echo "  git commit -am 'stackowl ${VER}' && git push"
```

---

## Release Workflow (end-to-end)

### Publishing a new version

```bash
# 1. Tag and push — triggers GitHub Actions (builds 3 tarballs, ~10 min)
git tag v0.2.0 && git push --tags

# 2. Wait for CI to complete and attach tarballs to the GitHub Release

# 3. Patch the formula
./scripts/update-formula.sh v0.2.0 ../homebrew-stackowl

# 4. Review and push
cd ../homebrew-stackowl
git diff Formula/stackowl.rb
git commit -am "stackowl 0.2.0" && git push
```

### Customer: first install

```bash
brew tap starskrime/stackowl
brew install stackowl
stackowl start
```

### Customer: upgrade

```bash
brew upgrade stackowl
```

---

## Files Changed / Created

| Repo | File | Action |
|------|------|--------|
| `stackowl-personal-ai-assistant` | `scripts/update-formula.sh` | Create |
| `homebrew-stackowl` | `Formula/stackowl.rb` | Create |
| `homebrew-stackowl` | `README.md` | Create |

No changes to existing CI, `build-release.sh`, or source code.
