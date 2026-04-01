#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# patch_visualizer.sh — Patch the visualizer for local development.
#
# Changes:
#   1. vite.config.ts: base path '/' instead of '/imc-prosperity-4-visualizer/'
#   2. vite.config.ts: proxy /runs to serve_runs.py (port 8080)
#   3. App.tsx: remove basename so routes work at root
#
# Run once after cloning or after git submodule update.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VIZ_DIR="$(dirname "$SCRIPT_DIR")/visualizer"

echo "Patching visualizer for local development..."

# --- 1. Patch vite.config.ts ---
cat > "$VIZ_DIR/vite.config.ts" << 'VITEEOF'
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Patched for local development (prosperity-workspace)
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    minify: false,
    sourcemap: true,
  },
  resolve: {
    alias: {
      '@tabler/icons-react': '@tabler/icons-react/dist/esm/icons/index.mjs',
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/runs': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
});
VITEEOF

echo "  [1/2] vite.config.ts patched (base: '/', proxy: /runs -> :8080)"

# --- 2. Patch App.tsx to remove basename ---
sed -i '' "s|basename: '/imc-prosperity-4-visualizer/',|// basename removed for local dev|g" "$VIZ_DIR/src/App.tsx"

echo "  [2/2] App.tsx patched (basename removed)"

echo ""
echo "Done. Run 'cd visualizer && pnpm install && pnpm dev' to start."
