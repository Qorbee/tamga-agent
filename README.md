# Tamga

**An AI finance operator for freight brokers, by Qorbee.** Tamga follows every shipment from the
conversations it already lives in — WhatsApp, email, invoices, CMRs and delivery photos — proposes who
gets paid and when under the owner's own written rules, and explains every payment. Money settles in
USDC on [Arc](https://www.arc.network/) through a contract that caps what the agent can move, so a
persuasive message can never talk it past its limits.

> In the steppe, a *tamga* proved what was yours. Tamga proves the job was done before anyone gets paid.

Built for the [Tameion agents hackathon](https://tameion.thecanteenapp.com/), beside a real freight
broker that runs it in shadow mode. **Status: in active development.** All payments are test USDC on
Arc testnet; nothing touches a company's bank or real money.

## How it works

1. **Evidence arrives from what the company already uses, read-only** — its mailboxes, its accounting
   system, WhatsApp items the owner shares, and one-tap links that drivers and dispatchers answer —
   and is linked to a project file: a job with a client above, subcontractors below, and one entry per
   truck. In *shadow mode* Tamga reads, drafts and proposes, and sends nothing itself.
2. **Specialist agents give opinions** — document extraction, match, risk, treasury — and a controller
   proposes one decision per payable, with its reasons.
3. **Plain code applies the hard rules**: no independent proof, no payment; never pay the same invoice
   twice; a changed payee wallet waits out a timelock; amounts above the owner's approval limit go to
   the owner, and nothing above the contract's caps can be paid at all.
4. **The owner approves each payment as it becomes due**, card by card, in a chat-first app on their
   phone, or asks the assistant why; late clients are chased on a cadence the owner sets.
5. **The executor settles** through the PolicyVault contract on Arc, and every decision lands in a
   hash-chained log anchored on-chain.

## What keeps it safe

- **Agents propose, code decides, the contract enforces.** No model output moves money. Only the
  policy engine's executor reaches the payment path, after the hard rules pass, and PolicyVault
  enforces the caps on-chain. No chat tool, prompt or inbound message can call it.
- **Text from outside is data, never instructions.** A mail, a WhatsApp message or a document never
  selects a tool, a payee or an amount on its own.
- **Every business row belongs to one company**, and every query filters by it. A judge's playground
  sandbox is a company of its own.
- **Shadow and replay modes change nothing outside Tamga**: no message sent, no mail marked read, no
  record written in a connected system. The mode is checked where the effect happens, not in a prompt.
- **Testnet by default.** The network, the chain id, the RPC and every address come from settings;
  mainnet needs an explicit opt-in.

## Where it stands

"How it works" is the whole design. Built and running today:
- **Sign-in and tenancy:** companies, owners by invite, and guest sandboxes, seeded with a freight
  job and its simulated books, that expire after a day; every row belongs to one company.
- **Company modes:** shadow, replay and live, checked where an effect would happen, and each
  company's own business time.
- **Mail, read-only:** mailboxes read over IMAP every two minutes, grouped into conversations, shown
  as their senders laid them out (web pictures only on request), and searchable however a word is
  typed. Each mail is read once, copies from other mailboxes included, from what its writer typed
  (the history it quotes cut off), and code reads its plates, IBANs and invoice, order and CMR
  numbers.
- **Files:** uploads and mail attachments, stored once and opened in a canvas beside the chat, which
  zooms, turns, renames, deletes an upload and goes back to the mail a file came from: PDFs,
  spreadsheets as Excel lays them out, images, archives and `winmail.dat` as folders of their files,
  Word files as the PDF an isolated LibreOffice makes of them, web pages in a sandboxed frame, XML
  and text as text. Each is read once, in a locked child process: a PDF's text layer and a
  spreadsheet's visible cells, with hidden text left out and counted, and the facts code finds in
  them.
- **Scans and photos:** a vision model reads the pages and pictures that have no text layer, the
  pictures a mail's body shows included, in a call with no tools, and marks what it cannot read.
- **Sorting:** code and a small model sort each conversation into work, automated mail and noise,
  so only work is read further.
- **Parties:** the companies and people Tamga meets, recognised by phone, email, tax id and IBAN.
- **The project file:** each job holds its mail, files and invoices on one timeline. A file
  follows the mail that carried it, unless it is only a signature's picture or a logo. Code
  types the truck papers it recognises and a small model the rest (a weighbridge slip, a customs
  declaration, a packing list), so each row says what the paper is. Bills are
  placed in their job by the numbers and plates they share with it. Code proposes the job's
  trucks from its papers, and each truck shows what it still lacks: a loading photo, the CMR, the
  proof of delivery, the subcontractor's invoice. Only the owner marks a file as a truck's proof
  of delivery, in one tap; the owner edits a project where it is shown.
- **Linking:** each work conversation is placed among the projects, by code where a shared number
  or its thread decides, else by an agent with read-only search tools, a small model first and a
  larger one for the hard cases. It links, proposes a new project, asks between two, or leaves the
  conversation unplaced, and writes its reason. Everything it writes waits for the owner, who
  confirms, rejects, moves a conversation or merges two projects in the Inbox, and reads the
  reason in either language.
- **The chat:** chats are kept on the server, and the assistant answers in them from the
  company's own mail, projects, files, invoices and firms, streamed over AG-UI. When the owner
  asks, it changes Tamga's own records through the services the pages use: it confirms or
  rejects what Tamga tied to a job, puts a paper on a truck, adds a truck, opens or edits a
  project. It never moves money, sends anything or marks a proof of delivery. The
  history a run reads is the server's, never the browser's; an answer is finished and kept when
  the page that asked is closed; unread is per person. A ring beside Send shows how full a chat
  is, and before it fills its earlier part is written into notes; a model names each chat from
  its first question, in one small request; whoever asks chooses the model and how long it
  reasons.
- **Tamga writes first:** when it proposes a project or cannot choose one for a conversation, it
  writes to the owner in the chat that already holds that record, or opens a new one, and code
  puts a card under its message, on which the owner answers. Such a turn acts for the company
  as an actor with no user, which every service that writes refuses.
- **Live:** what the background work writes shows without a reload, through the company's event
  stream, and a note says when Tamga writes, also as the browser's notification.
- **Budgets:** each use of a model has a daily budget per company that is zero until the owner
  sets it, so nothing a company holds is read by a model before that.
- **The books:** a read-only accounting sync, through a country pack.
- **Circle:** the wallet set and the company treasury on Arc testnet, developer-controlled. Each
  company gets an account on the vault, with an owner key and a receive-only wallet for each
  subcontractor it pays. A judge's sandbox is opened on the vault with test USDC, and what is
  left of it goes back to the treasury when it expires, sent by the executor, Circle's Agent
  Wallet.
- **PolicyVault:** the contract that caps the executor, written, tested, and deployed on Arc
  testnet through Circle: [`0x6354dbccc4f5063fd10b9b1e4ff66d65d4224d0a`](https://testnet.arcscan.app/address/0x6354dbccc4f5063fd10b9b1e4ff66d65d4224d0a),
  its source verified there, with Tamga's
  [treasury wallet](https://testnet.arcscan.app/address/0x42f1180241d22e93dfec51f68e8a59fe8752623a) as its admin. One vault holds every
  company, each with its own balance, caps, payees and paid keys. A change needs the company's
  owner key to sign it, and one that loosens a limit waits out a timelock.

Next, in this order: the daily digest, the draft cards and a page of the books; the policy
engine, its decisions and their log, with the approval in the chat; the executor paying bills
from the vault; the replay of past projects with its score, and the playground's scenarios.

## This repository

The public copy of Tamga's private repository, generated at every merge: each commit here is one
merged pull request, dated when it merged. It holds the core — the agents and the policy engine, mail
and document intake, the logistics pack, the owner app and the judges' playground, Circle wallets and
PolicyVault. Country integrations (a pilot's accounting system, national identifiers and e-documents)
and the internal design documents stay in the private repository.

## Running locally

Needs Docker, [uv](https://docs.astral.sh/uv/), Node (LTS) and pnpm. Once per machine, the supply-chain
settings for one-off tools:

```bash
scripts/setup-machine.sh            # shows what it would change; --apply to change it
```

If another project already uses 5432 or 8333, copy `.env.example` to `.env` at the root and change
the port there first, then use the same port in `backend/.env`. The root `.env` also moves 8000 and
3000, but only for the images under compose's `app` profile. Run directly, as below, the two dev
servers take a flag instead: `uvicorn … --port 8001`, `pnpm dev -p 3001`. Then set the API URL in
`frontend/.env`, and `CORS_ORIGINS` and `WEB_URL` in `backend/.env`, to match.

Run directly, the backend opens archives with the system's libarchive: on Debian or Ubuntu
`sudo apt install libarchive13t64` (`libarchive13` before Ubuntu 24.04 and Debian 13); on macOS
`brew install libarchive`, then `export LIBARCHIVE=$(brew --prefix libarchive)/lib/libarchive.dylib`.
The backend image installs it.

```bash
docker compose up -d                               # Postgres, file storage and the converter
curl -X PUT http://localhost:8333/tamga            # once, on your port if you moved 8333: the
                                                   # storage creates no bucket itself
cd backend && cp .env.example .env && uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload               # http://localhost:8000/docs
```

In a second terminal, the worker, which runs the background jobs: it polls the connected mailboxes
every 2 minutes (`mail.poll`) and reads each new mail once (`mail.process`), syncs a connected
accounting system every 6 hours (`books.sync`), and deletes expired guest sandboxes
(`sandboxes.expire`). Without it no mail arrives.

```bash
cd backend && uv run watchfiles "python -m app.worker" app   # restarts when the code changes
```

In a third, the web app:

```bash
cd frontend && cp .env.example .env && pnpm install
pnpm dev                                           # http://localhost:3000
```

**Getting in.** Open http://localhost:3000 and choose *Try it as a guest*: a private sandbox of your
own, deleted after a day. For a company of your own, create it with its first owner; the command
prints the invite link where the owner sets a password:

```bash
cd backend && uv run python scripts/create_company.py --name "Acme Lojistik" --owner-email owner@example.com
```

**What turns on what.** Every setting is in `backend/.env.example`, with a comment. Unset, Tamga
still runs, and each feature waits for its own:
- mail: `PILOT_COMPANY_ID` and `MAILBOXES` (read-only IMAP);
- the language models: `OPENAI_API_KEY`, the key of the provider `DEFAULT_MODEL` names (only
  OpenAI's client is installed; an Anthropic model needs its extra added first);
- Circle wallets on Arc testnet: `CIRCLE_API_KEY` and `CIRCLE_ENTITY_SECRET`, then
  `uv run python scripts/create_circle_wallets.py` once, from `backend/`, and copy the two lines it
  prints (`CIRCLE_WALLET_SET_ID`, `CIRCLE_TREASURY_WALLET_ID`) into `backend/.env`: run again
  without them, it makes another wallet set;
- traces: `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`;
- Word files shown and read as PDFs: `CONVERTER_URL`, already set in `.env.example` for the
  converter `docker compose up -d` starts (its image is about 1.5 GB, pulled once).

Contracts need Foundry at the release in `contracts/.foundry-version` and the library submodules
(`git submodule update --init`) — [`contracts/README.md`](contracts/README.md).

## Checks

The ones CI runs, each from its package's directory:

```bash
# backend/, with docker compose up; the tests include the API contract check
uv run ruff format --check . && uv run ruff check . && uv run mypy && INTEGRATION=1 uv run pytest -q
uv run alembic upgrade head && uv run alembic check   # the models match the migrations
# frontend/: the generated API types must match backend/openapi.json (no diff after generating)
pnpm generate:api && pnpm lint && pnpm typecheck && pnpm build
# contracts/, then scripts/export-abi.sh from the root: no diff in backend/app/platform/abi/
forge fmt --check && forge build --sizes && forge test
```

If the root `.env` moved the ports, export `DATABASE_URL` (to the `app_test` database) and
`S3_ENDPOINT_URL` with them before the backend tests (`backend/README.md`).

## Layout

| Path | What is there |
|---|---|
| [`backend/`](backend/README.md) | The API service (FastAPI, PostgreSQL): agents, policy engine, intake, the worker |
| [`frontend/`](frontend/README.md) | The web app (Next.js, shadcn/ui, assistant-ui): landing page, the chat-first owner app, the playground |
| [`contracts/`](contracts/README.md) | Solidity with Foundry: PolicyVault, the contract that caps the executor |
| `docker-compose.yml` | Local Postgres, file storage and the converter; the backend and web images under `--profile app` |
| [`deploy/`](deploy/README.md) | The production Compose file laid over it, and the server's steps |
| `scripts/` | `setup-machine.sh` (per-machine supply-chain settings); `export-abi.sh` (contract ABIs into the backend); `find_hidden_unicode.py` (invisible characters, in CI and pre-commit) |
| `.github/` | CI and the pull-request checklist |

## Supply chain

| Layer | Where |
|---|---|
| Nothing younger than 7 days is installed | `backend/pyproject.toml` (`exclude-newer`), `frontend/pnpm-workspace.yaml` (`minimumReleaseAge`); Foundry and contract libraries pinned by hand |
| …including one-off tools (`uvx`, `pnpm dlx`/`create`, `npx`), which ignore project config | `scripts/setup-machine.sh --apply`, once per machine |
| No trust downgrades, no git/tarball transitive deps, no unapproved install scripts | `frontend/pnpm-workspace.yaml` |
| Actions and pre-commit hooks pinned to commit SHAs; read-only CI token | `.github/workflows/ci.yml`, `.pre-commit-config.yaml` |
| Known CVEs in the lockfiles; hidden Unicode in instruction files | CI `audit` job (weekly and on every push) |

## License

Proprietary: all rights reserved — see [LICENSE](LICENSE). The hackathon's organizers and judges may
run it to evaluate the project; any other use needs written consent (support@qorbee.com).
Third-party components keep their own licences ([THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)).
