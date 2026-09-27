# Frontend

The Tamga web app: the landing page, the owner app (a chat with a canvas) and, later, the playground —
one Next.js App Router project on Tailwind v4, shadcn/ui and assistant-ui. It holds no business rules;
everything it shows comes from the FastAPI backend through the generated API contract. How it fits
together: `docs/architecture/07-web-app.md`.

## Map

| Path | What is there |
|---|---|
| `src/app/(marketing)/` | The landing page (`/`) |
| `src/app/(app)/app/` | The owner app: rail, chat home, list pages, and the `@canvas` slot |
| `src/app/globals.css` | The design tokens, mapped onto shadcn's variables — the only place colours are defined |
| `src/features/` | Domain UI: `shell/` (rail, API status), `assistant/` (chat home, runtime) |
| `src/components/ui/` | shadcn/ui components (Radix, `radix-maia` style) |
| `src/components/assistant-ui/` | assistant-ui components from its registry |
| `src/components/icons.tsx` | Lucide names drawn with Hugeicons, for registry components |
| `src/lib/` | `env.ts` (the only env reader), `api.ts` (the only `fetch`), `api-schema.d.ts` (generated), `i18n/`, `format.ts` |
| `scripts/check-contrast.mjs` | Fails `pnpm lint` when a text token drops below WCAG AA |

## Start reading at

`src/app/(app)/app/layout.tsx` for the shell, then `src/features/assistant/assistant-home.tsx` — the
chat runs on a placeholder runtime until the AG-UI chat service lands (ADR-0006).

## Running it

With the backend running (`backend/README.md`):

```bash
cp .env.example .env
pnpm install
pnpm dev                    # http://localhost:3000 — the dot at the bottom of the rail turns green
```

The image: `docker build --build-arg NEXT_PUBLIC_API_URL=https://api.tamga.qorbee.com -t tamga-web .`

## Checks

```bash
pnpm lint && pnpm typecheck && pnpm build     # lint includes the contrast check
pnpm generate:api                             # after a backend API change
```

## Local edits to registry code

Components added with `shadcn add` are ours to edit; re-adding one overwrites these changes, so
re-apply them:

- `components/assistant-ui/**`: icon imports point at `@/components/icons`, not `lucide-react`.
- `components/assistant-ui/**`, `ui/dialog.tsx`: every label comes from `t.chat` (`useI18n()`);
  overlays and controls laid over a picture use the `scrim` tokens, not black and white.
- `thread.aui.tsx`: a `placeholder` prop, defaulting to the assistant's placeholder.
- `markdown-text.tsx`: `useShallowStable` uses state, not a ref read during render (React Compiler).
- `thread.aui.tsx`, `attachment.aui.tsx`, `tool-fallback.aui.tsx`: adapted to `@assistant-ui/react`
  0.15.21, which lacks `submission` and `dismissible`. Upgrading to 0.15.22+ (Oct 1): bump the
  package and put those two back by hand from the registry's version — re-adding the files would
  drop every edit above.

## Rules that apply here

`AGENTS.md` in this directory and the root `AGENTS.md`.
