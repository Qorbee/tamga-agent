# Frontend

The Tamga web app: the landing page, the owner app (a chat with a canvas) and the playground — one
Next.js App Router project on Tailwind v4, shadcn/ui and assistant-ui. It holds no business rules;
everything it shows comes from the FastAPI backend through the generated API contract.

## Map

| Path | What is there |
|---|---|
| `src/app/(marketing)/` | The landing page (`/`) |
| `src/app/(app)/app/` | The owner app: its layout (chat runtime, sidebar, canvas), chat home, list pages, and the `@canvas` slot |
| `src/app/globals.css` | The design tokens for both themes, mapped onto shadcn's variables — the only place colours are defined |
| `src/app/(auth)/` | Sign-in and invite pages, outside the app shell |
| `src/features/` | Domain UI: `shell/` (sidebar, canvas, account menu, Search, Settings), `assistant/` (chat runtime, chat home, chat title), `session/` (sign-in, invite, guest entry, the `/app` guard), `projects/` (the board), `upload/`, `inbox/` |
| `src/components/ui/` | shadcn/ui components (Radix, `radix-maia` style) |
| `src/components/assistant-ui/` | assistant-ui components from its registry |
| `src/components/icons.tsx` | Lucide names drawn with Hugeicons, for registry components |
| `src/lib/` | `env.ts` (the only env reader), `api.ts` (the only `fetch`), `api-schema.d.ts` (generated), `i18n/`, `format.ts` |
| `scripts/check-contrast.mjs` | Fails `pnpm lint` when a text token drops below WCAG AA |

## Start reading at

`src/app/(app)/app/layout.tsx` for the shell, then `src/features/assistant/chat-runtime.tsx`: the
chats and what was said in them are the server's, and the answer is the chat protocol's stream
(AG-UI, ADR-0006), read by assistant-ui's runtime for it.

## Running it

With the backend running (`backend/README.md`):

```bash
cp .env.example .env
pnpm install
pnpm dev                    # http://localhost:3000 — the sidebar's foot shows who is signed in, or "Tamga is offline"
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
- `components/assistant-ui/**`, `ui/dialog.tsx`, `ui/sheet.tsx`, `ui/sidebar.tsx`: every label comes
  from `t.chat` or `t.shell` (`useI18n()`); overlays and controls laid over a picture use the `scrim`
  tokens, not black and white.
- `thread.aui.tsx`: a `placeholder` prop, defaulting to the assistant's placeholder; the viewport
  scrolls only when it overflows (`overflow-y-auto`, a stable gutter) and starts below the page bar.
- `thread-list.aui.tsx`: rows styled for the sidebar (`sidebar-accent`), and the open chat is
  highlighted only inside `[data-chat-open]`, so it does not compete with the open page. Chats
  this user has not read come first, under their own label, with a dot (`lib/use-chats.ts`); a
  chat the server still answers in shows as running. Archived chats sit under their own row
  (`ThreadListArchived`), where the menu brings one back. Delete opens `DeleteChatDialog` first; a long title
  fades out (`truncate-fade`, in `globals.css`) instead of ending in "…". A row is its own named group
  (`group/thread`): the sidebar's wrapper is an unnamed `group`, so a plain `group-hover` showed every
  row's "…" whenever the pointer was anywhere in the sidebar.
- `thread.aui.tsx`: one text size (`text-sm`) for messages and the composer; messages share the
  composer's edges; a turn is tight and turns are further apart; a 48 rem column.
- `markdown-text.tsx`: `useShallowStable` uses state, not a ref read during render (React Compiler);
  links are neutral, not terracotta. `ui/button.tsx`: the `link` variant, likewise.
- `ui/sidebar.tsx`: no width animation while a resize handle inside it is dragged
  (`group-has-data-dragging/sidebar-wrapper:transition-none`); `SidebarContent` shows its scrollbar
  (no `no-scrollbar`) with a stable gutter.
- `ui/sidebar.tsx`: on a phone the drawer covers the whole screen (`SIDEBAR_WIDTH_MOBILE`).
- `tool-group.aui.tsx`, `tool-fallback.aui.tsx`, `reasoning.aui.tsx`: a step that opens or closes
  holds the scroll with `keep-in-place.ts`, not assistant-ui's `useScrollLock`, which hid the
  scrollbar for the animation and made the page jump beside it.
- `hooks/use-mobile.ts`: reads the media query through `useSyncExternalStore` instead of setting state
  in an effect, which the React Compiler lint rejects.
- `thread.aui.tsx`: no Edit on a question and no Refresh on an answer. The server keeps one
  history a chat and takes only a new message, so either would ask the same thing again.
- `thread.aui.tsx`, `tool-group.aui.tsx`, `tool-fallback.aui.tsx`: a group of tool calls is named
  by what they did, from `t.assistant.tools` ("Opened a project ×3"), and a message that only
  looked things up sits close to the next one with no action bar. An answer that failed on the
  server (`run_failed`) is said in the reader's words. A message with no words (a card that
  stands alone) has no action bar either. `components` takes `ComposerTools`, drawn beside
  Send (the model and effort chip, and the chat's context meter).
- `thread.aui.tsx`, `attachment.aui.tsx`, `tool-fallback.aui.tsx`: written against
  `@assistant-ui/react` 0.15.21, which lacked `submission` and `dismissible`. The package is now
  0.15.22; the two are not put back, since nothing asks for an approval yet. When approvals come,
  take them by hand from the registry's version: re-adding the files would drop every edit above.
