"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, type ComponentProps, type ReactNode } from "react";

import {
  formatTrail,
  formatView,
  FROM_PARAM,
  parseTrail,
  parseView,
  sameView,
  VIEW_PARAM,
  type CanvasView,
} from "@/lib/canvas-view";

// Whether the open record was opened by a link in this tab, so an entry in the history leads back to
// the page without it. Opened from a reload or a pasted address, there is no such entry.
let openedByLink = false;

// Set around what the canvas shows, so a link in it opens the next record with the way back.
const InCanvas = createContext(false);

/** What the canvas shows: a link inside it keeps the way back to the record it is in. */
export function CanvasScope({ children }: { children: ReactNode }) {
  return <InCanvas.Provider value={true}>{children}</InCanvas.Provider>;
}

/** The record open in the canvas, from the address. */
export function useCanvasView(): CanvasView | null {
  return parseView(useSearchParams().get(VIEW_PARAM));
}

/** The records the open one was opened from, oldest first. */
function useCanvasTrail(): CanvasView[] {
  return parseTrail(useSearchParams().get(FROM_PARAM));
}

function useHrefWith(view: CanvasView | null, trail: readonly CanvasView[] = []): string {
  return hrefWith(usePathname(), useSearchParams(), view, trail);
}

function hrefWith(
  pathname: string,
  search: URLSearchParams,
  view: CanvasView | null,
  trail: readonly CanvasView[],
): string {
  const params = new URLSearchParams(search);
  params.delete(FROM_PARAM);
  if (view) {
    params.set(VIEW_PARAM, formatView(view));
    if (trail.length > 0) params.set(FROM_PARAM, formatTrail(trail));
  } else params.delete(VIEW_PARAM);
  // Colons and commas are safe in a query; left unescaped, the address reads `?view=mail:…`.
  const query = params.toString().replace(/%3A/gi, ":").replace(/%2C/gi, ",");
  return query ? `${pathname}?${query}` : pathname;
}

/**
 * Opens a record in the canvas beside the current page. From a closed canvas it adds a history entry,
 * so the phone's back button closes it again; from an open one it replaces the record shown, so
 * closing never walks back through what was open before. A link inside the canvas keeps the way
 * back to the record it is in, for the panel's back button.
 */
export function CanvasLink({
  view,
  onClick,
  ...props
}: Omit<ComponentProps<typeof Link>, "href" | "replace" | "scroll"> & { view: CanvasView }) {
  const current = useCanvasView();
  const trail = useCanvasTrail();
  const inside = useContext(InCanvas);
  const href = useHrefWith(view, inside && current && !sameView(current, view) ? [...trail, current] : []);
  return (
    <Link
      {...props}
      href={href}
      replace={current !== null}
      scroll={false}
      aria-current={sameView(current, view) ? "true" : undefined}
      onClick={(event) => {
        if (current === null) openedByLink = true;
        onClick?.(event);
      }}
    />
  );
}

/**
 * Forgets that a link opened the record once the canvas closes or the app's pages are left, however
 * that happened: the back button, a sidebar link, the sign-in page when the session runs out. The
 * entry behind a record shown again after that is not known to be the page without it.
 */
export function useForgetOpenedByLink(open: boolean): void {
  useEffect(() => {
    if (!open) openedByLink = false;
  }, [open]);
  useEffect(
    () => () => {
      openedByLink = false;
    },
    [],
  );
}

/** Shows the record the open one was opened from, or null when it was opened from the page. */
export function useCanvasBack(): { view: CanvasView; go: () => void } | null {
  const router = useRouter();
  const trail = useCanvasTrail();
  const previous = trail.at(-1) ?? null;
  const href = useHrefWith(previous, trail.slice(0, -1));
  const go = useCallback(() => router.replace(href, { scroll: false }), [router, href]);
  return previous ? { view: previous, go } : null;
}

/** Shows a record in place of the open one, with no way back to it: the open one is gone. */
export function useShowInCanvas(): (view: CanvasView) => void {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  return useCallback(
    (view: CanvasView) => router.replace(hrefWith(pathname, search, view, []), { scroll: false }),
    [router, pathname, search],
  );
}

/** Closes the canvas and leaves the page under it as it was. */
export function useCloseCanvas(): () => void {
  const router = useRouter();
  const without = useHrefWith(null);
  return useCallback(() => {
    if (openedByLink) {
      openedByLink = false;
      router.back();
    } else {
      router.replace(without, { scroll: false });
    }
  }, [router, without]);
}
