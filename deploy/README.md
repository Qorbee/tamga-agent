# deploy/

Tamga on its Hetzner server, behind Nginx Proxy Manager (NPM):
01 System context, Deploy. `compose.yml` is laid over the
root `docker-compose.yml`, which stays the one place the images and versions are written. It adds
production's differences: no published port, R2 instead of the local file storage, Postgres with a
real password, the schema migrated before the api and the worker start, and restarts. `.env.example`
holds the three variables Compose itself needs; the apps read only `backend/.env`. `tamga` is
`docker compose` with both files, `deploy/.env` and the app profile, from any directory: every
command below starts with it.

## Once per server

Needs Docker with Compose 2.24.4 or later (`docker compose version`), for the `!reset` and
`!override` tags, and NPM already running.

1. Clone the repository with a read-only deploy key.
2. `install -m 600 deploy/.env.example deploy/.env`, readable by its owner alone like the next
   one, and fill it in:
   - `POSTGRES_PASSWORD`: from `openssl rand -hex 24`;
   - `NPM_NETWORK`: NPM's network, from `docker network ls`.
3. `install -m 600 backend/.env.example backend/.env`: it will hold the mailbox passwords and every
   key. Set, besides the keys each feature needs (the root README, "What turns on what"):
   - `APP_ENV=production`;
   - `WEB_URL=https://tamga.qorbee.com`;
   - `CORS_ORIGINS=https://tamga.qorbee.com,https://play.tamga.qorbee.com`;
   - `SESSION_COOKIE_DOMAIN=tamga.qorbee.com`, the parent the web and API hosts share;
   - `FORWARDED_ALLOW_IPS`: NPM's network's subnet, from `docker network inspect <NPM_NETWORK>`,
     or every guest shares one address and one rate limit;
   - R2: `S3_ENDPOINT_URL` (`https://<account id>.r2.cloudflarestorage.com`), `S3_BUCKET`,
     `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`.

   `DATABASE_URL` and the worker's `CONVERTER_URL` come from `compose.yml`, and the settings refuse
   to start without the rest.
4. `deploy/tamga up -d --build`. The first build pulls the converter's image (about 1.5 GB) and builds
   the api and web images.
5. In NPM, three proxy hosts, each with a Let's Encrypt certificate and HTTP/2:

   | Host | Forward to |
   |---|---|
   | `tamga.qorbee.com` | `web`, port 3000 |
   | `play.tamga.qorbee.com` | `web`, port 3000 |
   | `api.tamga.qorbee.com` | `backend`, port 8000, with WebSockets on |

   On `api.`, the advanced tab holds what a streamed chat and an upload need (nginx's own limit
   is 1 MB; `UPLOAD_MAX_BYTES` is 25 MB):
   `proxy_buffering off; proxy_read_timeout 3600s; client_max_body_size 30m;`.
6. The first company and its owner:
   `deploy/tamga run --rm backend python scripts/create_company.py --name "…" --owner-email …`
   prints the invite link where the owner sets a password.
7. The backups' folder, readable by root alone, and the daily dump (Backups, below):
   `sudo install -d -m 700 /var/backups/tamga`.

## Updating

```bash
git pull
deploy/tamga up -d --build
```

`migrate` runs `alembic upgrade head` first; the api and the worker start only once it has
succeeded. If it fails, they keep running the old image: `deploy/tamga logs migrate` says why.

## Checking it

- `curl https://api.tamga.qorbee.com/health/ready`: the database and the file storage;
- `deploy/tamga ps`: every service up, `migrate` exited 0;
- `deploy/tamga logs -f backend worker`.

## Rolling back

Check out the previous commit and run `deploy/tamga up -d --build`. When the release being undone
added a migration, first undo it with the new image, still built: `deploy/tamga run --rm migrate
alembic downgrade <revision before it>`. **A downgrade that drops a column drops its data: back up first.**

## Backups

The database is the one thing here not kept elsewhere (files are in R2). Once a day, from root's
crontab on the server (`sudo crontab -e`), into the folder step 7 made; `%` is cron's newline, so
it is escaped:

```bash
15 3 * * * /srv/tamga/deploy/tamga exec -T postgres pg_dump -U postgres app | gzip > /var/backups/tamga/app-$(date +\%F).sql.gz
```

(`/srv/tamga` is where the repository was cloned.) Restore into an empty database with
`gunzip -c <file> | deploy/tamga exec -T postgres psql -U postgres app`.
