"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, type ComponentProps } from "react";

import { formatView, parseView, sameView, VIEW_PARAM, type CanvasView } from "@/lib/canvas-view";

// Whether the open record was opened by a link in this tab, so an entry in the history leads back to
// the page without it. Opened from a reload or a pasted address, there is no such entry.
let openedByLink = false;

/** The record open in the canvas, from the address. */
export function useCanvasView(): CanvasView | null {
  return parseView(useSearchParams().get(VIEW_PARAM));
}

function useHrefWith(view: CanvasView | null): string {
  const pathname = usePathname();
  const params = new URLSearchParams(useSearchParams());
  if (view) params.set(VIEW_PARAM, formatView(view));
  else params.delete(VIEW_PARAM);
  // A colon is safe in a query; left unescaped, the address reads `?view=mail:…`.
  const query = params.toString().replace(/%3A/gi, ":");
  return query ? `${pathname}?${query}` : pathname;
}

/**
 * Opens a record in the canvas beside the current page. From a closed canvas it adds a history entry,
 * so the phone's back button closes it again; from an open one it replaces the record shown, so
 * closing never walks back through what was open before.
 */
export function CanvasLink({
  view,
  onClick,
  ...props
}: Omit<ComponentProps<typeof Link>, "href" | "replace" | "scroll"> & { view: CanvasView }) {
  const current = useCanvasView();
  const href = useHrefWith(view);
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
