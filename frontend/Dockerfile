# syntax=docker/dockerfile:1
# One image for every environment. Runtime configuration comes from the environment; NEXT_PUBLIC_*
# values are inlined into browser JavaScript at build time, so they are build arguments instead.
#
# The Node major must match .node-version (CI reads that file); pnpm comes from package.json's
# packageManager field through Corepack, so neither version is repeated here.

FROM node:24-bookworm-slim AS base
ENV PNPM_HOME=/pnpm \
    NEXT_TELEMETRY_DISABLED=1
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable
WORKDIR /app


FROM base AS deps
# pnpm-workspace.yaml carries the supply-chain settings (release age, build approvals).
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile


FROM base AS build
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build


FROM node:24-bookworm-slim
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app

# The standalone server does not serve public/ or .next/static on its own; copy both beside it.
COPY --from=build --chown=app /app/.next/standalone ./
COPY --from=build --chown=app /app/.next/static ./.next/static
COPY --from=build --chown=app /app/public ./public

USER app
EXPOSE 3000
CMD ["node", "server.js"]
