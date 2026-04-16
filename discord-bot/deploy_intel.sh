#!/bin/bash
# Deploy Discord intel data to GitHub Pages (gh-pages branch)
# Run from repo root: ./discord-bot/deploy_intel.sh

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

INTEL_JSON="discord-bot/storage/claude_intel.json"
MESSAGES_JSON="discord-bot/storage/messages.json"
INTEL_HTML=".github/discord-intel.html"

if [ ! -f "$INTEL_JSON" ]; then
  echo "ERROR: $INTEL_JSON not found. Run the bot first."
  exit 1
fi

echo "[deploy] Deploying intel to gh-pages..."

TMPDIR=$(mktemp -d)
trap "cd '$REPO_ROOT' && git worktree remove '$TMPDIR' 2>/dev/null || rm -rf '$TMPDIR'" EXIT

# Clone gh-pages into a worktree
git fetch origin gh-pages 2>/dev/null || true
git worktree add "$TMPDIR" origin/gh-pages 2>/dev/null

cd "$TMPDIR"
git checkout -B gh-pages origin/gh-pages 2>/dev/null || git checkout -B gh-pages

# Create discord-intel directory and copy files
mkdir -p discord-intel
cp "$REPO_ROOT/$INTEL_JSON" discord-intel/claude_intel.json
[ -f "$REPO_ROOT/$MESSAGES_JSON" ] && cp "$REPO_ROOT/$MESSAGES_JSON" discord-intel/messages.json
[ -f "$REPO_ROOT/discord-bot/storage/digest.json" ] && cp "$REPO_ROOT/discord-bot/storage/digest.json" discord-intel/digest.json
cp "$REPO_ROOT/$INTEL_HTML" discord-intel.html

# Update main dashboard with intel link
if [ -f index.html ] && ! grep -q "discord-intel.html" index.html; then
  # Inject link if not already present
  sed -i '' 's|<h1>Prosperity Terminal</h1>|<h1>Prosperity Terminal</h1><a href="./discord-intel.html" style="color:#59c2ff;font-size:12px;text-decoration:none;border:1px solid #1a3a5c;padding:2px 8px;border-radius:3px">Discord Intel</a>|' index.html
fi

git add -A
if git diff --cached --quiet; then
  echo "[deploy] No changes to deploy."
else
  git commit -m "Update Discord intel $(date +%Y-%m-%d_%H:%M)"
  git push origin gh-pages
  echo "[deploy] Intel deployed."
fi

echo "[deploy] Done. View at: https://chennethelius.github.io/slu-imc-prosperity-4/discord-intel.html"
