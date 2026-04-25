#!/usr/bin/env bash
# Apply local backtester patches to the submodule's working tree.
#
# Patches are upstream-pending changes we want every teammate (and CI) to
# run with, but that haven't been merged into the GeyzsoN/prosperity_rust_backtester
# repo we pin to. Each .patch file in scripts/patches/ that starts with
# "backtester_" is applied here. Re-running this script is a no-op when the
# patch is already applied.
#
# Usage:
#   ./scripts/patch_backtester.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUBMODULE="$REPO_ROOT/backtester"
PATCH_DIR="$REPO_ROOT/scripts/patches"

if [ ! -d "$SUBMODULE" ]; then
    echo "  No backtester submodule at $SUBMODULE — run 'git submodule update --init' first."
    exit 1
fi

shopt -s nullglob
patches=("$PATCH_DIR"/backtester_*.patch)
shopt -u nullglob

if [ ${#patches[@]} -eq 0 ]; then
    echo "  No backtester patches in $PATCH_DIR. Nothing to do."
    exit 0
fi

for patch in "${patches[@]}"; do
    name="$(basename "$patch")"
    if (cd "$SUBMODULE" && git apply --check --reverse "$patch") 2>/dev/null; then
        echo "  $name: already applied"
        continue
    fi
    if (cd "$SUBMODULE" && git apply --check "$patch") 2>/dev/null; then
        (cd "$SUBMODULE" && git apply "$patch")
        echo "  $name: applied"
    else
        echo "  $name: FAILED to apply (submodule may have moved). Inspect manually."
        exit 1
    fi
done
