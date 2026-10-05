#!/usr/bin/env bash
# CI's checks, run on this machine: the merge gate while GitHub Actions has no minutes left.
# .github/workflows/ci.yml stays the source; a step added there is added here.
#
#   scripts/check-all.sh                  # every job
#   scripts/check-all.sh backend audit    # some of them: backend, frontend, contracts, audit
#
# Needs `docker compose up -d --wait` and the PostgreSQL client tools (createdb, dropdb). The
# backend tests run on a database of their own, never the one you develop against, at the ports
# the root .env gives compose. Unlike CI, backend/.env is read too: a test that depends on a
# setting sets it itself.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

# Only the host ports are taken from the root .env.
port() { { grep -E "^$1=" .env 2>/dev/null || true; } | tail -n 1 | cut -d= -f2- | tr -d '"'; }
pg_port="$(port POSTGRES_PORT)"
pg_port="${pg_port:-5432}"
s3_port="$(port STORAGE_PORT)"
s3_port="${s3_port:-8333}"
converter_port="$(port CONVERTER_PORT)"
converter_port="${converter_port:-3100}"
server="postgresql+asyncpg://postgres:postgres@localhost:${pg_port}"

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# Through the host port, not `docker compose exec`: from a worktree, compose would look for a
# project named after the folder.
pg() { PGPASSWORD=postgres "$@" -h localhost -p "$pg_port" -U postgres; }

# This run's own databases, named so no other database is ever dropped (the tests' name ends in
# _test, as their harness requires), and dropped again however the run ends.
run_id="check_$$_$(date +%s)"
migrations_db="${run_id}_migrations"
test_db="${run_id}_test"
drop_ours() { pg dropdb --if-exists "$migrations_db" 2>/dev/null; pg dropdb --if-exists "$test_db" 2>/dev/null; }

backend() {
  step "backend: lint, types, migrations, tests"
  trap drop_ours EXIT
  pg createdb "$migrations_db"
  pg createdb "$test_db"
  (
    cd backend
    uv sync --locked
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy
    DATABASE_URL="$server/$migrations_db" uv run alembic upgrade head
    DATABASE_URL="$server/$migrations_db" uv run alembic check
    DATABASE_URL="$server/$test_db" S3_ENDPOINT_URL="http://localhost:${s3_port}" \
      CONVERTER_URL="http://localhost:${converter_port}" INTEGRATION=1 uv run pytest -q
  )
}

frontend() {
  step "frontend: API types, lint, types, build"
  (
    cd frontend
    pnpm install --frozen-lockfile
    # As CI does, but an .env of your own is kept.
    if [ ! -f .env ] && [ -f .env.example ]; then cp .env.example .env; fi
    pnpm generate:api
    if [ -n "$(git status --porcelain -- src/lib/api-schema.d.ts)" ]; then
      git diff -- src/lib/api-schema.d.ts
      echo "src/lib/api-schema.d.ts is stale or uncommitted. Run: pnpm generate:api" >&2
      exit 1
    fi
    pnpm lint
    pnpm typecheck
    # CI builds with the committed example alone. Your .env is left as it is (a running dev
    # server reads it), so a variable only it sets fails here, and the example's values are
    # given to the build, over the file's.
    names() { grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$1" | tr -d = | sort -u; }
    only_yours="$(comm -23 <(names .env) <(names .env.example))"
    if [ -n "$only_yours" ]; then
      echo "frontend/.env sets $(echo $only_yours), which .env.example lacks: CI builds without it" >&2
      exit 1
    fi
    (
      set -a
      . ./.env.example
      set +a
      pnpm build
    )
  )
}

contracts() {
  step "contracts: format, build, tests, ABI"
  # Each library is pinned at a stable release tag of its own repository, as CI checks. The pin
  # checked is the commit's, so Forge must build the library at that commit, not at a checkout
  # moved since.
  git config -f .gitmodules --get-regexp '^submodule\..*\.path$' | while read -r key path; do
    url="$(git config -f .gitmodules --get "${key%.path}.url")"
    sha="$(git rev-parse "HEAD:$path")"
    if [ "$(git -C "$path" rev-parse HEAD)" != "$sha" ]; then
      echo "$path is checked out at another commit than HEAD pins: commit the bump, or run" \
        "git submodule update" >&2
      exit 1
    fi
    refs="$(git ls-remote --tags "$url")"
    tags="$(awk -v sha="$sha" '$1 == sha { sub("^refs/tags/", "", $2); sub("\\^\\{\\}$", "", $2); print $2 }' <<<"$refs" | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' || true)"
    if [ -z "$tags" ]; then
      echo "$path is at $sha, which is not a stable release tag of $url" >&2
      exit 1
    fi
    echo "$path: $sha = $(echo $tags)"
  done
  (
    cd contracts
    forge fmt --check
    forge build --sizes
    forge test
  )
  scripts/export-abi.sh
  if [ -n "$(git status --porcelain -- backend/app/platform/abi)" ]; then
    git status --porcelain -- backend/app/platform/abi
    echo "backend/app/platform/abi/ is stale or uncommitted. Run: scripts/export-abi.sh" >&2
    exit 1
  fi
}

audit() {
  step "audit: hidden Unicode, known vulnerabilities"
  python3 scripts/find_hidden_unicode.py .
  requirements="$(mktemp)"
  trap 'rm -f "$requirements"' RETURN
  (cd backend && uv export --locked --no-hashes --format requirements-txt > "$requirements")
  uvx --exclude-newer "7 days" pip-audit --disable-pip --no-deps -r "$requirements"
  (cd frontend && pnpm audit --prod --audit-level high)
}

jobs=("$@")
[ ${#jobs[@]} -eq 0 ] && jobs=(backend frontend contracts audit)
for job in "${jobs[@]}"; do
  case "$job" in
    backend | frontend | contracts | audit) "$job" ;;
    *)
      echo "unknown job: $job (backend, frontend, contracts, audit)" >&2
      exit 2
      ;;
  esac
done
step "every check passed: ${jobs[*]}"
