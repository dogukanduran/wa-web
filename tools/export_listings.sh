#!/usr/bin/env bash
# Export Ghidra listings for WA.exe into analysis/WA.exe (git-ignored), which
# game.toml's [translate] listings points at. Runs Ghidra's auto-analysis, then
# tools/ExportAsmFast.java (assembly only, no per-function decompile).
#
#   GHIDRA_HOME=/path/to/ghidra_12.1.3_PUBLIC tools/export_listings.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GHIDRA_HOME="${GHIDRA_HOME:-${GHIDRA_INSTALL_DIR:-}}"

if [ -z "$GHIDRA_HOME" ] || [ ! -x "$GHIDRA_HOME/support/analyzeHeadless" ]; then
  echo "Set GHIDRA_HOME to an extracted Ghidra 12.1.3 directory" >&2
  exit 1
fi
if [ ! -f "$ROOT/original/WA.exe" ]; then
  echo "original/WA.exe is missing; run kit/tools/setup.py --link-only first (see CONTRIBUTING.md)" >&2
  exit 1
fi

mkdir -p "$ROOT/analysis/ghidra"
"$GHIDRA_HOME/support/analyzeHeadless" "$ROOT/analysis/ghidra" ArmageddonRecomp \
  -import "$ROOT/original/WA.exe" -overwrite \
  -scriptPath "$ROOT/tools" -postScript ExportAsmFast.java "$ROOT/analysis"

test -s "$ROOT/analysis/WA.exe/functions.tsv" || { echo "Ghidra exported no function index" >&2; exit 1; }
echo "Listings ready in analysis/WA.exe. Next: .venv/bin/python tools/build.py --target headless --regenerate"
