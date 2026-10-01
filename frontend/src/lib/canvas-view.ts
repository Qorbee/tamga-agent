/**
 * What the canvas shows, kept in the page's address as `?view=<kind>:<id>` (`/app/inbox?view=mail:…`).
 * The page under it stays what the path says, so a reload or a shared link opens both, and opening
 * another record changes one parameter instead of stacking pages.
 *
 * A record opened from inside the canvas (a mail's attachment) keeps the way back in `?from=`: the
 * records it was opened from, oldest first (`?view=document:…&from=mail:…`).
 */

export const VIEW_PARAM = "view";
export const FROM_PARAM = "from";
// The deepest a trail goes: a mail, an archive in it, a folder in that, the file.
const MAX_TRAIL = 3;

const KINDS = ["mail", "document"] as const;

export type CanvasView = { kind: (typeof KINDS)[number]; id: string };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The view a `?view=` value names, or null for none or one this app does not know. */
export function parseView(value: string | null): CanvasView | null {
  const [kind, id] = value?.split(":") ?? [];
  const known = KINDS.find((name) => name === kind);
  return known && id && UUID.test(id) ? { kind: known, id } : null;
}

export function formatView(view: CanvasView): string {
  return `${view.kind}:${view.id}`;
}

export function sameView(a: CanvasView | null, b: CanvasView | null): boolean {
  return a?.kind === b?.kind && a?.id === b?.id;
}

/** The records a `?from=` value names, oldest first; a trail naming one this app does not know is dropped. */
export function parseTrail(value: string | null): CanvasView[] {
  const trail: CanvasView[] = [];
  for (const part of value?.split(",") ?? []) {
    const view = parseView(part);
    if (!view) return [];
    trail.push(view);
  }
  return trail.slice(-MAX_TRAIL);
}

export function formatTrail(trail: readonly CanvasView[]): string {
  return trail.slice(-MAX_TRAIL).map(formatView).join(",");
}
