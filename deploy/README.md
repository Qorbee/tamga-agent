# deploy/

Tamga on its Hetzner server, behind Nginx Proxy Manager (NPM):
01 System context, Deploy. `compose.yml` is laid over the
root `docker-compose.yml`, which stays the one place the images and versions are written. It adds
production's differences: no published port, R2 instead of the local file storage, Postgres with a
real password and the apps signed in as a role of their own, the schema migrated before the api and
the worker start, a memory cap per service, and restarts. `.env.example` holds the variables
Compose itself and `tamga` need; the apps read only `backend/.env`. `tamga` is `docker compose`
with both files, `deploy/.env` and the app profile, from any directory, plus commands of its own:
`update`, `rollback`, `backup`, `roles` and `sql`. Every command below starts with it.

## The server

What the steps below take for granted:
- **SSH only over a private network** (Tailscale on this server), and a firewall in front of the
  server that opens 80 and 443 to the internet and nothing Tamga needs besides. Docker publishes
  ports around `ufw`, so the firewall in front is the one that counts; Tamga publishes none.
- **Memory:** the caps in `compose.yml` add up to about 4.8 GB (Postgres 1 GB, api 768 MB, worker
  1.5 GB, web 512 MB, converter 1 GB), and 5.5 GB during an update, while `migrate` (768 MB) runs
  beside them; the services use far less when idle. On a server with
  little free memory and no swap, add swap first, or the kernel kills a process at the first
  large document:
  `sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile &&
  sudo swapon /swapfile`, then `/swapfile none swap sw 0 0` in `/etc/fstab`.
- Every image has an ARM build, so an ARM server is fine.
- `gpg` on the host (`sudo apt install gnupg`; Ubuntu has it), which encrypts the dumps that leave.

## Once per server

Needs Docker with Compose 2.24.4 or later (`docker compose version`), for the `!reset` and
`!override` tags, and NPM already running.

1. Clone the repository with a read-only deploy key.
2. `install -m 600 deploy/.env.example deploy/.env`, readable by its owner alone like the next
   one, and fill it in:
   - `POSTGRES_PASSWORD` and `TAMGA_DB_PASSWORD`, each from `openssl rand -hex 24`: the
     superuser's and the apps' own role's (below, Database roles);
   - `DOMAIN`: `tamga.qorbee.com`, the one place the addresses come from (below, Changing the
     domain);
   - `NPM_NETWORK`: NPM's network, from `docker network ls`.
3. `backend/.env`, readable by its owner alone: it will hold the mailbox passwords and every key.
   Copy the development machine's over SSH (`scp`, then `chmod 600`), which holds every key the
   features need, and drop its `DATABASE_URL` and `CONVERTER_URL`; or start from
   `install -m 600 backend/.env.example backend/.env` and add the keys each feature needs (the root
   README, "What turns on what"). Either way, set:
   - `APP_ENV=production`;
   - `FORWARDED_ALLOW_IPS`: NPM's network's subnet, from `docker network inspect <NPM_NETWORK>`,
     or every guest shares one address and one rate limit;
   - R2: `S3_ENDPOINT_URL` (`https://<account id>.r2.cloudflarestorage.com`), `S3_BUCKET`,
     `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`;
   - `ARC_RPC_URL`: the team's own node (`arc-canteen rpc-url`); with the vault set, the settings
     refuse the public one.

   `DATABASE_URL`, the worker's `CONVERTER_URL`, and `WEB_URL`, `CORS_ORIGINS` and
   `SESSION_COOKIE_DOMAIN` (from `DOMAIN`) come from `compose.yml`, over any line `backend/.env`
   has; the settings refuse to start without the rest.
   - **The agents' models:** `AGENT_MODELS` and `AGENT_EFFORTS` as on the development machine,
     which put the background agents, the summarizer and the chat's helpers on the small model;
     an agent they leave out runs on `DEFAULT_MODEL`.
   - **The backup key:** the public half of a key pair made on the owner's own machine, so the
     server can encrypt each dump it sends off but never read one (below, Backups):
     ```bash
     gpg --quick-generate-key "Tamga backups" default default never   # asks for a passphrase
     gpg --armor --export "Tamga backups" > backup-key.asc            # the public key: to the server
     gpg --armor --export-secret-keys "Tamga backups"                 # the private key: a password manager
     ```
     then `scp backup-key.asc` into the server's `deploy/`. Without the private key and its
     passphrase no off-server dump can be read again, so keep them somewhere that is not this
     server.
4. `deploy/tamga roles`: starts Postgres and makes the apps' role and the read-only one. When a
   development machine already read a company's mail and books, move its data now (below,
   "Moving the development data"). Then `deploy/tamga up -d --build`. The first build pulls the
   converter's image (about 1.5 GB) and builds the api and web images.
5. DNS: an A record for each host below (and AAAA if the server has IPv6) pointing at the
   server, since Let's Encrypt checks them. Then, in NPM, three proxy hosts, each with a Let's
   Encrypt certificate and HTTP/2:

   | Host | Forward to |
   |---|---|
   | `tamga.qorbee.com` | `web`, port 3000 |
   | `play.tamga.qorbee.com` | `web`, port 3000 |
   | `api.tamga.qorbee.com` | `backend`, port 8000, with WebSockets on |

   On `api.`, the advanced tab holds what a streamed chat and an upload need (nginx's own limit
   is 1 MB; `UPLOAD_MAX_BYTES` is 25 MB):
   `proxy_buffering off; proxy_read_timeout 3600s; client_max_body_size 30m;`.
6. The first company and its owner, unless they came with the moved data:
   `deploy/tamga run --rm backend python scripts/create_company.py --name "…" --owner-email …`
   prints the invite link where the owner sets a password. Once its books are synced, its
   account on the vault: `deploy/tamga run --rm backend python scripts/open_vault_account.py
   <company id> --deposit … --per-payment-cap … --daily-cap … --timelock …` prints what it would
   register; the same with `--write` opens it, from the treasury's test USDC.
7. The executor's sign-in to Circle's CLI, in the worker (ADR-0017), with the same email as the
   Agent Wallet the vault names as its executor (`AGENT_WALLET_ADDRESS`), so Circle reuses that
   wallet: `deploy/tamga exec worker circle wallet login <email> --type agent --testnet`, then the
   code Circle mails, and its terms when asked. `deploy/tamga exec worker circle wallet status`
   shows it signed in. The session lasts seven days and lives on the `circle_session` volume, so a
   new image keeps it; until it is renewed, payments wait and nothing is marked paid.
8. One dump by hand, `deploy/tamga backup --offsite`, to see it reach R2 encrypted, then the daily
   one from the crontab of the user that cloned the repository (Backups, below).

## Moving the development data

Once, at the first deploy, when the server takes over from a development machine that already read
a company's mail and books: its database and files move, so nothing is read or paid for again.
Stop the development machine's worker first and leave it stopped: two workers would read the same
mailboxes into two databases, and both would anchor on the vault.

1. On the development machine, from the repository root:
   `docker compose exec -T postgres pg_dump -U postgres -Fc app > tamga.dump`, then `scp` it to the
   server. It holds the company's mail: delete both copies once restored.
2. On the server, after step 4's `roles` and before its `up`:
   `deploy/tamga exec -T postgres pg_restore -U postgres -d app --no-owner --role=tamga
   --single-transaction --exit-on-error < tamga.dump`, so every table belongs to the apps' role.
   Step 4's `migrate` brings it to the newest revision.
3. The files, from the development machine into step 3's R2 bucket under the same keys, so each
   row finds its file: `cd backend && uv run python scripts/copy_files.py --endpoint <R2 endpoint>
   --bucket <bucket> --access-key-id <key id>`, which asks for the secret key. It reads this
   machine's storage from `S3_*` in `backend/.env` or the environment; a stopped run is run again.
4. Step 6 is skipped: the company and its owner came with the database, and the owner signs in
   with the same password.

## Updating

```bash
deploy/tamga update            # origin/main; or a commit or tag: deploy/tamga update <ref>
```

In order, it:
1. stops if the checkout has local changes;
2. dumps the database and copies the dump to R2, encrypted (below, Backups), with the script of
   the release it leaves;
3. notes the last release that came up healthy in `deploy/.previous-release`;
4. checks out the new one, detached, so a rollback is a checkout too;
5. builds the images while the old containers still serve;
6. runs the migration on its own, with the old containers still serving. If it fails, nothing
   that serves has changed: the log says why, and `deploy/tamga rollback` checks the old code
   out again;
7. replaces the containers and waits up to a minute for `/health/ready`. Not healthy, it prints
   the services and the last logs and stops. Healthy, it notes the release in
   `deploy/.current-release`, which the next update goes back to.

The data outlives every update: the database is the `postgres_data` volume and the Circle session
the `circle_session` volume, which a new image or container keeps; the files are in R2. Only
`deploy/tamga down -v` or removing a volume by hand loses them.

## Checking it

- `curl https://api.tamga.qorbee.com/health/ready`: the database and the file storage;
- `deploy/tamga ps`: every service up, `migrate` exited 0;
- `deploy/tamga logs -f backend worker`;
- `deploy/tamga exec worker circle wallet status`: the executor signed in for testnet (step 7).
- `deploy/tamga sql "select status, count(*) from procrastinate_jobs group by 1"`: the database,
  read-only (below, Database roles).
- `deploy/tamga run --rm --no-deps backend python scripts/judge_path.py --api https://api.<DOMAIN>
  --web https://<DOMAIN>`: a judge's whole path on the live site, from a guest's sandbox to a bill
  paid on Arc, each step timed. It opens a real sandbox and pays its bill in test USDC.

## Database roles

`deploy/tamga roles` makes them, and every release runs it again, so Postgres always checks the
password `deploy/.env` holds:
- **`tamga`**, the apps': the api, the worker and the migration sign in as it. It owns the
  database and its tables, so it migrates, but it is no superuser: it cannot read the server's
  files, run programs or touch another database, and it cannot sign in as `postgres`, whose
  password the apps never see.
- **`tamga_readonly`**, for looking into what happened: `deploy/tamga sql "<query>"`, or a query on
  stdin. It reads every table and has no privilege to write one; its transactions are read-only
  and a query stops after 30 seconds, by default; it has no password, so it signs in only from
  inside the Postgres container, through docker. `sql` runs psql in restricted mode, so a
  backslash command (`\!` would run a shell) is refused and only SQL gets through. Each statement
  it runs is in `deploy/tamga logs postgres`, which keeps about 30 MB: so a query filters by id,
  never by a name, an email or an IBAN written into it.

**What the read-only role is for:** a query that cannot change anything by mistake, and a log of
what was read. It is no wall against whoever runs it: that user is in the `docker` group, which can
sign in as `postgres` anyway, and a session may lift its own defaults. Who may reach the server at
all (Tailscale, the firewall) is the wall.

`postgres`, the superuser, stays for the container's own setup and for dumps, restores and roles.
`roles` also hands the apps anything `postgres` still owns in the schema, as after a restore
without `--role=tamga`.

**Changing `TAMGA_DB_PASSWORD`:** the new line in `deploy/.env`, then `deploy/tamga roles` and
`deploy/tamga up -d`, on their own rather than inside an `update`, so the running containers get
the new password as soon as Postgres checks it.

## Rolling back

`deploy/tamga rollback` checks out the last release that came up healthy before the last update,
and builds and starts it as `update` does. When the release being undone migrated the database,
it stops and lists the revisions:
- **Older code on a newer schema** works when the migration only added tables or columns:
  `deploy/tamga rollback --keep-schema` starts the older code on the database as it is, with no
  migration, since the older code does not know the newer revisions.
- **Otherwise** undo the migration first, with the newer code still checked out:
  `deploy/tamga run --rm migrate alembic downgrade <revision before it>`, then
  `deploy/tamga rollback --keep-schema`. **A downgrade that drops a column drops its data:** the
  dump `update` made before it is the way back (Backups, restoring).

## Backups

The database is the one thing here not kept elsewhere (files are in R2). `deploy/tamga backup`
writes a custom-format dump into `BACKUP_DIR` (`deploy/backups`, ignored by git and readable by
its owner alone) and
removes those older than `BACKUP_DAYS` (14); `--offsite` also copies it into the R2 bucket under
`backups/` (`backend/scripts/store_backup.py`) and removes the copies there past the same age. The
dump holds every company's mail, so it goes to no other place, and **only encrypted**: to the
public key at `BACKUP_KEY` (`deploy/backup-key.asc`, step 3), with gpg. Without that key
`--offsite` and `update` stop before doing anything. The copy on the server stays plain, for a
quick restore: whoever can read it can read the database itself. `update` takes one first. Once a
day, from the crontab of the user that cloned the repository (`crontab -e`):

```bash
15 3 * * * $HOME/Repositories/tamga/deploy/tamga backup --offsite >> "$HOME/tamga-backup.log" 2>&1
```

(`$HOME/Repositories/tamga` is where the repository was cloned.)

**Restoring** replaces everything since the dump. A dump kept only in R2 is downloaded first from
the bucket's `backups/` (Cloudflare's dashboard) to the machine holding the private key, decrypted
there (`gpg --output app.dump --decrypt app-….dump.gpg`), and copied to the server. Stop what
writes, make the database empty and the apps' own, restore in one transaction that stops at the
first error, bring the schema up to the code, start again:

```bash
deploy/tamga stop backend worker
deploy/tamga exec -T postgres dropdb -U postgres app
deploy/tamga exec -T postgres createdb -U postgres app
deploy/tamga roles
deploy/tamga exec -T postgres pg_restore -U postgres -d app --no-owner --role=tamga --single-transaction --exit-on-error < <dump>
deploy/tamga run --rm migrate
deploy/tamga start backend worker
```

## Changing the domain

Every address comes from `DOMAIN` in `deploy/.env`: the web app on it and on `play.`, the API on
`api.`, the invite links, the browsers allowed to call the API, and the session cookie's domain.
To move: the DNS records and NPM's proxy hosts for the new names (step 5), the new `DOMAIN`, then
`deploy/tamga update`, which builds the web image again since the API's address is in it. Every
session ends, since its cookie belonged to the old domain.
