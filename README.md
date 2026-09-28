# Tamga

**An AI finance operator for freight brokers, by Qorbee.** Tamga follows every project file from the
conversations it already lives in — WhatsApp, email, invoices, CMRs and delivery photos — proposes who
gets paid and when under the owner's own written rules, and explains every payment. Money settles in
USDC on [Arc](https://www.arc.network/) through a contract that caps what the agent can move, so a
persuasive message can never talk it past its limits.

> In the steppe, a *tamga* proved what was yours. Tamga proves the job was done before anyone gets paid.

Built for the [Tameion agents hackathon](https://tameion.thecanteenapp.com/). **Status: in active
development** — see the roadmap. All payments are test USDC on Arc
testnet; nothing touches a company's bank or real money.

## How it works

1. **Evidence arrives on the channels people already use** — WhatsApp, email, web upload — and is
   linked to a project file (a job with a client above and subcontractors below).
2. **Specialist agents give opinions** — document extraction, match, risk, treasury — and a controller
   proposes one decision per payable, with its reasons.
3. **Plain code applies the hard rules** (no independent proof, no payment; never pay the same invoice
   twice; a changed payee wallet waits out a timelock; amounts above the owner's approval limit go to
   the owner, and nothing above the contract's caps can be paid at all).
4. **The owner approves the weekly payment run** card by card, in a chat-first app, or asks the
   assistant why.
5. **The executor settles** through the PolicyVault contract on Arc, and every decision lands in a
   hash-chained log anchored on-chain.

The design and its reasoning: `docs/architecture/` and
`docs/adr/`.

## Running it locally

Needs Docker, [uv](https://docs.astral.sh/uv/), Node (LTS) and pnpm. Once per machine, the supply-chain
settings for one-off tools:

```bash
scripts/setup-machine.sh            # shows what it would change; --apply to change it
```

Then:

```bash
docker compose up -d                               # Postgres
cd backend && cp .env.example .env && uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload               # http://localhost:8000/docs
```

In another terminal:

```bash
cd frontend && cp .env.example .env && pnpm install
pnpm dev                                           # http://localhost:3000
```

Contracts need Foundry at the release in `contracts/.foundry-version` and the library submodules
(`git submodule update --init`) — [`contracts/README.md`](contracts/README.md).

Once per clone: `uvx --exclude-newer "7 days" pre-commit install`.

## Layout

| Path | What is there |
|---|---|
| `AGENTS.md` | Rules for every coding agent: stack, contracts, product invariants, definition of done, review rules |
| `CLAUDE.md` (and in each package) | One line, `@AGENTS.md` — how Claude Code loads the same rules |
| `.agents/skills/` | Project skills. `.claude/skills` is a symlink to this directory |
| [`backend/`](backend/README.md) | The API service (FastAPI, PostgreSQL) |
| [`frontend/`](frontend/README.md) | The web app (Next.js, shadcn/ui, assistant-ui): landing page and the chat-first owner app |
| [`contracts/`](contracts/README.md) | Solidity with Foundry: PolicyVault, the contract that caps the executor |
| `docs/` | Architecture, ADRs, plans, guides, page templates |
| `docker-compose.yml` | Local Postgres; the backend and web images under `--profile app` |
| `scripts/` | `setup-machine.sh` (per-machine supply-chain settings); `export-abi.sh` (contract ABIs into the backend) |
| `.github/` | CI, Dependabot, the `main` ruleset, and the PR checklist (the definition of done) |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Branches, Conventional Commits, PRs |

## Supply chain

| Layer | Where |
|---|---|
| Nothing younger than 7 days is installed | `backend/pyproject.toml` (`exclude-newer`), `frontend/pnpm-workspace.yaml` (`minimumReleaseAge`); Foundry and contract libraries pinned by hand (`contracts/AGENTS.md`) |
| …including one-off tools (`uvx`, `pnpm dlx`/`create`, `npx`), which ignore project config | `scripts/setup-machine.sh --apply`, once per machine |
| No trust downgrades, no git/tarball transitive deps, no unapproved install scripts | `frontend/pnpm-workspace.yaml` |
| Actions and pre-commit hooks pinned to commit SHAs; read-only CI token | `.github/workflows/ci.yml`, `.pre-commit-config.yaml` |
| Updates only after a 7-day cooldown; security fixes immediately | `.github/dependabot.yml` |
| Known CVEs in the lockfiles; hidden Unicode in instruction files | CI `audit` job (weekly and on every PR), pre-commit |

A security patch needed before it is 7 days old is the one exception: exempt that single package, with
the advisory and a removal date (`AGENTS.md`, "Supply chain").

## Agent instructions and skills

`AGENTS.md` is the single source of agent rules; each `CLAUDE.md` contains only `@AGENTS.md`, so Codex,
Claude Code and most other agents read the same copy. Nested files (`backend/AGENTS.md`,
`frontend/AGENTS.md`, `contracts/AGENTS.md`) add the rules for that package. Skills in
`.agents/skills/` follow the [Agent Skills](https://agentskills.io/specification) format:

| Skill | What it does |
|---|---|
| `init-stack` | Recommend and apply a stack from the product documents; update every seam a slot touches |
| `agents-md` | Write, prune or test `AGENTS.md` / `CLAUDE.md` for signal density |
| `adr` | Propose a decision record with options and a confirmation check; stop for a human decision |
| `security-audit` | Adversarial audit of the diff: OWASP 2025, supply chain, agent config, LLM risks; no edits |
| `optimization-audit` | Measured performance, cost and dead-code audit written to `OPTIMIZATIONS.md` |

## License

[MIT](LICENSE)
