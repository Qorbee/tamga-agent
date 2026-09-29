/**
 * How the owner left the sidebar and the canvas: open or collapsed, and the widths they dragged them
 * to. They live in cookies rather than browser storage so the server renders them and the page never
 * jumps on load.
 */

/** Written by `components/ui/sidebar.tsx` when the sidebar opens or collapses. */
export const SIDEBAR_OPEN_COOKIE = "sidebar_state";

export type PanelSize = { cookie: string; min: number; max: number; initial: number };

export const SIDEBAR_SIZE: PanelSize = {
  cookie: "tamga_sidebar_width",
  min: 224,
  max: 420,
  initial: 272,
};

// The canvas is capped first by the room the page beside it leaves (`features/shell/canvas.tsx`).
export const CANVAS_SIZE: PanelSize = {
  cookie: "tamga_canvas_width",
  min: 360,
  max: 2400,
  initial: 560,
};

export function clampSize(size: PanelSize, width: number): number {
  return Math.min(size.max, Math.max(size.min, Math.round(width)));
}

/** A cookie's value as a width, or the initial width when it is missing or not a number. */
export function readSize(size: PanelSize, value: string | undefined): number {
  const width = Number(value);
  return value && Number.isFinite(width) ? clampSize(size, width) : size.initial;
}

export function saveSize(size: PanelSize, width: number): void {
  document.cookie = `${size.cookie}=${width}; path=/; max-age=31536000; samesite=lax`;
}
