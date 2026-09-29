/**
 * What the canvas shows, kept in the page's address as `?view=<kind>:<id>` (`/app/inbox?view=mail:…`).
 * The page under it stays what the path says, so a reload or a shared link opens both, and opening
 * another record changes one parameter instead of stacking pages.
 */

export const VIEW_PARAM = "view";

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
