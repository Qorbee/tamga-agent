#!/usr/bin/env bash
# Once per machine: the 7-day supply-chain delay for tools that run OUTSIDE a project.
#
# A project's own settings (backend/pyproject.toml `exclude-newer`, frontend/pnpm-workspace.yaml
# `minimumReleaseAge`) cover installs inside that project. One-off tools — `uvx`, `pnpm dlx`,
# `pnpm create`, `npx` — read only user-level config, so without this they fetch releases that are
# minutes old.
#
#   scripts/setup-machine.sh           # show what would change
#   scripts/setup-machine.sh --apply   # change it
#
# Safe to re-run: a setting that already exists is reported and left alone. Config files are written
# directly rather than through `pnpm config set`, because on a fresh machine running pnpm at all
# makes Corepack download the newest pnpm — the very thing this delays.
set -euo pipefail

apply=false
[ "${1:-}" = "--apply" ] && apply=true
missing=0

case "$(uname -s)" in
  Darwin)
    uv_dir="${XDG_CONFIG_HOME:-$HOME/.config}/uv"
    pnpm_dir="$HOME/Library/Preferences/pnpm" ;;
  MINGW* | MSYS* | CYGWIN*)
    uv_dir="$APPDATA/uv"
    pnpm_dir="$LOCALAPPDATA/pnpm/config" ;;
  *)
    uv_dir="${XDG_CONFIG_HOME:-$HOME/.config}/uv"
    pnpm_dir="${XDG_CONFIG_HOME:-$HOME/.config}/pnpm" ;;
esac

# ensure_setting <file> <regex matching the key> <line to add> <label>
ensure_setting() {
  local file=$1 key=$2 line=$3 label=$4
  if [ -f "$file" ] && grep -Eq "$key" "$file"; then
    printf 'ok       %-20s %s: %s\n' "$label" "$file" "$(grep -E "$key" "$file" | head -n 1)"
    return
  fi
  if ! $apply; then
    printf 'missing  %-20s would add `%s` to %s\n' "$label" "$line" "$file"
    missing=$((missing + 1))
    return
  fi
  mkdir -p "$(dirname "$file")"
  # Prepended, not appended: in TOML a top-level key after a [table] header would belong to it.
  {
    printf '%s\n' "$line"
    if [ -f "$file" ]; then cat "$file"; fi
  } > "$file.tmp"
  mv "$file.tmp" "$file"
  printf 'added    %-20s `%s` to %s\n' "$label" "$line" "$file"
}

ensure_setting "$uv_dir/uv.toml"       '^exclude-newer[[:space:]]*=' 'exclude-newer = "7 days"'  'uv, uvx'
ensure_setting "$pnpm_dir/config.yaml" '^minimumReleaseAge:'         'minimumReleaseAge: 10080'  'pnpm 11+, pnpm dlx'
ensure_setting "$pnpm_dir/rc"          '^minimum-release-age='       'minimum-release-age=10080' 'pnpm 10, pnpm dlx'
ensure_setting "$HOME/.npmrc"          '^min-release-age='           'min-release-age=7'         'npm, npx'

# Corepack fetches the newest pnpm for any project that doesn't pin one, with no age check.
# Projects pin theirs (package.json "packageManager"); this suggests a vetted machine default.
if command -v npm > /dev/null 2>&1 && command -v node > /dev/null 2>&1; then
  vetted=$(npm view pnpm time --json 2> /dev/null | node -e '
    let s = ""; process.stdin.on("data", (d) => (s += d)).on("end", () => {
      const times = JSON.parse(s), cutoff = Date.now() - 7 * 864e5;
      const key = (v) => v.split(".").map(Number);
      const newer = (a, b) => { const x = key(a), y = key(b);
        for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i]; return false; };
      let best = "";
      for (const [v, t] of Object.entries(times))
        if (/^\d+\.\d+\.\d+$/.test(v) && Date.parse(t) < cutoff && (!best || newer(v, best))) best = v;
      console.log(best);
    });') || vetted=""
  # Corepack's current default, read from its cache rather than by running pnpm (see the header).
  known_good="${COREPACK_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/node/corepack}/lastKnownGood.json"
  current=$(sed -n 's/.*"pnpm"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$known_good" 2> /dev/null || true)
  if [ -n "$vetted" ] && [ "$current" = "$vetted" ]; then
    printf '\nok       pnpm default          %s is the newest pnpm at least 7 days old\n' "$current"
  elif [ -n "$vetted" ]; then
    printf '\nNewest pnpm at least 7 days old: %s (default now: %s). To make it the machine default:\n' \
      "$vetted" "${current:-unknown}"
    printf '  corepack install --global pnpm@%s\n' "$vetted"
    printf 'It applies to every project without a "packageManager" in package.json — pin those first\n'
    printf '(`corepack use pnpm@<their current version>`) if they rely on .npmrc settings.\n'
  fi
fi

if [ "$missing" -gt 0 ]; then
  printf '\nNothing changed. Re-run with --apply to write the %d missing setting(s).\n' "$missing"
elif ! $apply; then
  printf '\nAll settings are in place.\n'
fi
