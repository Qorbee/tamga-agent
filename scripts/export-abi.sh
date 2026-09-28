#!/usr/bin/env bash
# Writes the ABI of each contract in contracts/src/ to backend/app/platform/abi/<Name>.json, where the
# backend's chain client reads it. The copies are generated, never edited: CI runs this script and
# fails when a committed copy differs (ADR-0001 — the same pattern as backend/openapi.json).
#
#   scripts/export-abi.sh        # after any change to a contract's functions, events or errors
#
# One contract per file, named after it: src/PolicyVault.sol holds PolicyVault. Needs Foundry
# (contracts/README.md).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/backend/app/platform/abi"

cd "$root/contracts"
# Start clean, so the copy of a deleted contract goes too.
rm -f "$dest"/*.json

shopt -s nullglob
for source in src/*.sol; do
  name="$(basename "$source" .sol)"
  mkdir -p "$dest"
  forge inspect "$source:$name" abi --json > "$dest/$name.json"
  echo "wrote backend/app/platform/abi/$name.json"
done
