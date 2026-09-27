# Full Stack Project Template

A starting point for full-stack projects whose stack changes from one project to the next. The
backend (Python, FastAPI, PostgreSQL, Redis) is ready to run; the frontend (Vite + React, Next.js or
Astro) is chosen per project and scaffolded by an agent skill, from your idea or PRD if you have one.
Agent rules and skills are plain `AGENTS.md` and `SKILL.md` files, so Codex, Claude Code and most
other coding agents read the same copy.

## Starting a new project

Once per machine (see [Supply chain](#supply-chain)):

```bash
scripts/setup-machine.sh            # shows what it would change; --apply to change it
```

Then:

```bash
gh repo create my-app --template yigitcanozdemir/full-stack-project-template --private --clone
cd my-app
```

(or click **Use this template** on this repository's GitHub page).

```bash
docker compose up -d                               # Postgres + Redis
cd backend && cp .env.example .env && uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload               # http://localhost:8000/docs
```

Put your idea note, PRD or research in `docs/product/` (any format), then run the **`init-stack`**
skill (`/init-stack` in Claude Code, `$init-stack` in Codex). It reads the documents, recommends a
stack with the requirement behind each choice, lists what the documents leave open, and — once you
confirm — refreshes the version snapshot, scaffolds the frontend, wires the generated API types,
drafts ADR-0001, and offers a first plan. Once per clone:
`uvx --exclude-newer "7 days" pre-commit install`.

Needs Docker, [uv](https://docs.astral.sh/uv/), Node (LTS) and pnpm.

## How it stays swappable

The root `AGENTS.md` separates **contracts** — one settings module per service, an API
contract generated from the backend (`backend/openapi.json` → `frontend/src/lib/api-schema.d.ts`),
generated migrations, one image per service — from **slots**: backend, database, cache, frontend.
Any slot can change as long as the contracts hold. The `init-stack` skill lists every file each slot
touches, and CI runs only the contract commands, so it works with whichever frontend is installed.

**Versions are written once each**, in the file that uses them: `backend/.python-version` (also what
the Docker image installs), `backend/uv.lock`, `docker-compose.yml` (CI starts the same file),
`frontend/.node-version`, `frontend/package.json`. Dependabot proposes updates; nothing else repeats
a version, so nothing goes stale.

## Supply chain

| Layer | Where |
|---|---|
| Nothing younger than 7 days is installed | `backend/pyproject.toml` (`exclude-newer`), `frontend/pnpm-workspace.yaml` (`minimumReleaseAge`) |
| …including one-off tools (`uvx`, `pnpm dlx`/`create`, `npx`), which ignore project config | `scripts/setup-machine.sh --apply`, once per machine |
| No trust downgrades, no git/tarball transitive deps, no unapproved install scripts | `frontend/pnpm-workspace.yaml` |
| Actions and pre-commit hooks pinned to commit SHAs; read-only CI token | `.github/workflows/ci.yml`, `.pre-commit-config.yaml` |
| Updates only after a 7-day cooldown; security fixes immediately | `.github/dependabot.yml` — also enable Dependabot alerts and security updates in the repo settings |
| Known CVEs in the lockfiles; hidden Unicode in instruction files | CI `audit` job (weekly and on every PR), pre-commit |

A security patch you need before it is 7 days old is the one exception: exempt that single package,
with the CVE and a removal date (`AGENTS.md`, "Supply chain").

## Layout

| Path | What is there |
|---|---|
| `AGENTS.md` | Rules for every coding agent: stack, contracts, supply chain, definition of done, review rules |
| `CLAUDE.md` (and in each package) | One line, `@AGENTS.md` — how Claude Code loads the same rules |
| `.agents/skills/` | Project skills. `.claude/skills` is a symlink to this directory |
| [`backend/`](backend/README.md) | The API service |
| [`frontend/`](frontend/README.md) | The web app — empty until `init-stack` runs |
| `docs/` | Product documents, architecture, ADRs, plans, guides, page templates |
| `docker-compose.yml` | Local Postgres and Redis; the backend image under `--profile app` |
| `scripts/setup-machine.sh` | Per-machine supply-chain settings |
| `.github/` | CI, Dependabot, and the PR checklist (the definition of done) |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Branches, Conventional Commits, PRs |

## Agent instructions and skills

**Instructions.** `AGENTS.md` is the single source. Codex, Cursor, GitHub Copilot, OpenCode and most
other agents read `AGENTS.md` files natively, from the root down to the directory they work in.
Claude Code reads `CLAUDE.md`, and each `CLAUDE.md` here contains only `@AGENTS.md`, which imports
the file beside it. Nested files (`backend/AGENTS.md`, `frontend/AGENTS.md`) add the rules for that
package.

**Skills** follow the open [Agent Skills](https://agentskills.io/specification) format: a folder
with a `SKILL.md` whose frontmatter has only `name` and `description`, which every tool understands.
They live in `.agents/skills/`, the project folder Codex, Cursor, GitHub Copilot, Gemini CLI,
OpenCode, Amp, Cline and Warp read. Claude Code reads `.claude/skills/`, a symlink to the same
folder. A tool with its own folder (Windsurf, Goose, Roo, Kiro, …) gets another symlink:
`ln -s ../.agents/skills .windsurf/skills`.

| Skill | What it does | Claude Code | Codex |
|---|---|---|---|
| `init-stack` | Read the idea/PRD, recommend and apply a stack, refresh versions, update every seam | `/init-stack` | `$init-stack` |
| `agents-md` | Write, prune or test `AGENTS.md` / `CLAUDE.md` for signal density | `/agents-md` | `$agents-md` |
| `adr` | Propose a decision record with options and a confirmation check; stop for a human decision | `/adr` | `$adr` |
| `security-audit` | Adversarial audit of the diff: OWASP 2025, supply chain, agent config, LLM risks; no edits | `/security-audit` | `$security-audit` |
| `optimization-audit` | Measured performance, cost and dead-code audit written to `OPTIMIZATIONS.md` | `/optimization-audit` | `$optimization-audit` |

Agents also pick a skill by themselves when a request matches its `description`.

**Using them in a repository not made from this template** (installs to `~/.agents/skills`, linked
into each tool's folder; run `setup-machine.sh` first so `npx` gets the 7-day rule too):

```bash
npx skills add yigitcanozdemir/full-stack-project-template -g -a claude-code -a codex \
  -s agents-md -s adr -s security-audit -s optimization-audit
npx skills update -g        # later: pull changes pushed to this repository
```

`init-stack` is left out on purpose, because it only makes sense inside this template's layout.
Treat third-party skills like code you are about to run: read them, and run
`.agents/skills/security-audit/scripts/find_hidden_unicode.py` on them, before installing.

**On another machine**, a project cloned from this template brings its rules and skills with it, so
there is nothing to install. Per machine, run `scripts/setup-machine.sh --apply` and the
`npx skills add … -g` commands again — neither is synced by git. On Windows, run
`git config --global core.symlinks true` with Developer Mode on *before* cloning; otherwise
`.claude/skills` is checked out as a plain text file.

## Documentation

Start at `docs/README.md`. The rules every change follows, including the definition
of done, are in `AGENTS.md`.
