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


FROM debian:trixie-slim

# CA certificates for outbound HTTPS (external APIs); nothing else from apt.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --no-create-home app

# Same paths as the build stage: the virtualenv's interpreter links point into /python.
COPY --from=build /python /python
COPY --from=build /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
