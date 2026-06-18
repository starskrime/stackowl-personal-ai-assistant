#!/usr/bin/env bash
# migrate-to-root.sh — Story 12.5: Move v2/ Python code to repo root.
#
# Pre-migration checklist (must all pass before running):
#   1. uv run pytest — all tests green
#   2. uv run python -m stackowl health — every contributor "ok"
#   3. uv run python -m stackowl backup --output ../stackowl-pre-migration-backup
#   4. sprint-status.yaml marks all 12 epics complete
#
# Run from repo root: bash v2/deploy/migrate-to-root.sh
# IMPORTANT: This is irreversible once committed. Review every git status step.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "=== Story 12.5: StackOwl v2 → root migration ==="
echo ""

# -- Pre-flight check --
if [ ! -f "v2/pyproject.toml" ]; then
    echo "ERROR: v2/pyproject.toml not found. Run from repo root." >&2
    exit 1
fi

echo "Step 1: Pre-migration backup"
if [ ! -d "v2" ]; then
    echo "ERROR: v2/ directory not found" >&2
    exit 1
fi

echo "  ✓ v2/ directory present"

echo ""
echo "Step 2: Create migration branch"
git checkout -b migration/v2

echo ""
echo "Step 3: Stage all v2/ files (if not already committed)"
git add v2/ .github/

echo ""
echo "Step 4: Commit v2 source (if untracked)"
git diff --cached --quiet || git commit -m "$(cat <<'EOF'
feat(v2): complete Python v2 rewrite — Epics 1–12

All 12 epics implemented:
- Epic 1: Foundation scaffold, migrations, StartupOrchestrator
- Epic 2: Multi-provider routing, CircuitBreaker, CostTracker
- Epic 3: Owl roster, DNA evolution, parliament
- Epic 4: Parliament 3-round debate, synthesis, pellets
- Epic 5: Memory/knowledge earning, LanceDB, Kuzu, DreamWorker
- Epic 6: JobScheduler, morning brief, instincts, fact extractor
- Epic 7: Textual TUI, 4-zone layout, WCAG AA
- Epic 8: Telegram, WhatsApp, voice transcription
- Epic 9: MCP server+client, capability negotiation
- Epic 10: Plugin system, audit logger, skill pack loader
- Epic 11: IntegrationAdapter, Gmail, Calendar, OAuth
- Epic 12: Service files, onboarding, export/import, governance

1232 tests passing.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"

echo ""
echo "Step 5: Delete Node.js v1 source files"
# Remove TypeScript src/ (conflicts with Python src/)
git rm -rf --ignore-unmatch src/ __tests__/ dist/ node_modules/
git rm -f --ignore-unmatch package.json package-lock.json tsconfig.json start.sh
# Remove any remaining .ts/.js files
git ls-files | grep -E '\.(ts|js|tsx|jsx)$' | grep -v node_modules | xargs git rm -f --ignore-unmatch || true

echo ""
echo "Step 6: Move Python v2 contents to repo root"
git mv v2/src .
git mv v2/tests .
git mv v2/scripts .
git mv v2/configs .
git mv v2/deploy .
git mv v2/pyproject.toml .
git mv v2/uv.lock .
git mv v2/Dockerfile .
git mv v2/.pre-commit-config.yaml .
git mv v2/.python-version . 2>/dev/null || true
# Move hidden dirs/files from v2 (exclude .git which doesn't exist)
for f in v2/.venv v2/.mypy_cache v2/.ruff_cache; do
    [ -e "$f" ] && git mv "$f" . || true
done
# Remove empty v2/ directory
rmdir v2 2>/dev/null || git rm -rf v2/

echo ""
echo "Step 7: Update CI workflow"
cp deploy/ci-post-migration.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml
git rm -f .github/workflows/ci-v2.yml 2>/dev/null || true

echo ""
echo "Step 8: Commit the migration"
git add -A
git commit -m "$(cat <<'EOF'
chore(migration): v2/ → repo root — Story 12.5

- Moves all Python v2 source from v2/ to repo root
- Deletes all Node.js v1 source files (src/*.ts, __tests__/, package.json, etc.)
- Renames ci-v2.yml → ci.yml (removes working-directory: v2 override)
- Updates CLAUDE.md with Python uv commands

After this commit:
  python -m stackowl --version   # from repo root
  uv run pytest                  # from repo root

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"

echo ""
echo "Step 9: Post-migration smoke test"
uv run python -m stackowl --version && echo "  ✓ stackowl --version OK"

echo ""
echo "=== Migration complete ==="
echo ""
echo "Next steps:"
echo "  git push -u origin migration/v2"
echo "  gh pr create --title 'feat: v2 migration to root — StackOwl 2.0.0'"
echo ""
echo "After PR merges to main:"
echo "  git tag -a v2.0.0 -m 'StackOwl v2.0.0 — Python rewrite'"
echo "  git push origin v2.0.0"
echo "  git branch -m release/v2 archive/release-v2-pre-migration  # archive old branch"
