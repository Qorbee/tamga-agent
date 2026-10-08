# syntax=docker/dockerfile:1
# One image for every environment; configuration comes only from environment variables.
#
# Python is whatever .python-version says: uv installs that interpreter (checksum-verified), so the
# version is written in one file and the image cannot drift from local development.

FROM debian:trixie-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed

WORKDIR /app

# Interpreter and dependencies before source, so a code change reuses these layers.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=.python-version,target=.python-version \
    uv python install

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=.python-version,target=.python-version \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


# Circle's CLI, which the executor runs from the worker (ADR-0017): its tree exactly as the
# committed lockfile has it, install scripts off. Node's major is the frontend's (.node-version);
# Docker cannot read that file, so the two move together by hand.
FROM node:24-trixie-slim AS circle

WORKDIR /circle-cli
RUN --mount=type=cache,target=/root/.npm \
    --mount=type=bind,source=circle-cli/package.json,target=package.json \
    --mount=type=bind,source=circle-cli/package-lock.json,target=package-lock.json \
    npm ci --omit=dev --ignore-scripts --no-audit --no-fund


FROM debian:trixie-slim

# CA certificates for outbound HTTPS (external APIs); libpq for psycopg, the job queue's driver
# (its pure-Python build loads the system library); libarchive for libarchive-c, which reads
# archives in the locked child (ADR-0012); libatomic for Node, copied in below without its own
# image's packages, which it needs on ARM. Nothing else from apt.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates libpq5 libarchive13t64 libatomic1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --no-create-home app

# Same paths as the build stage: the virtualenv's interpreter links point into /python.
COPY --from=build /python /python
COPY --from=build /app /app
# Node alone (no npm) and the CLI's tree. Its sign-in lives in CIRCLE_CLI_HOME, a volume of the
# worker's, so a new image keeps it; the human signs in there (deploy/README.md).
COPY --from=circle /usr/local/bin/node /usr/local/bin/node
COPY --from=circle /circle-cli/node_modules /circle-cli/node_modules
RUN install -d -o app /circle-session

ENV PATH="/app/.venv/bin:/circle-cli/node_modules/.bin:$PATH" \
    CIRCLE_CLI_HOME=/circle-session \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER app

EXPOSE 8000
# The company event stream holds a response open for minutes: on a stop, open streams are cut
# after five seconds, so the app's own shutdown (tracing flushed, the listener closed) still runs
# inside Docker's ten.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "5"]
