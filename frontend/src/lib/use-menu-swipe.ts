"use client";

import { useEffect } from "react";

// How far a finger must travel, and how close to the screen's left edge a swipe that opens the
// menu must start (the browser's own back gesture keeps the very edge on some phones).
const DISTANCE = 60;
const EDGE = 32;

/**
 * A phone's swipes for the menu: from the left edge to the right opens it, to the left anywhere
 * closes it. Mostly sideways only, so a scroll never counts; a swipe that starts in something that
 * scrolls sideways (a row of chips, a table) is left to it.
 */
export function useMenuSwipe({ enabled, open, setOpen }: { enabled: boolean; open: boolean; setOpen: (open: boolean) => void }) {
  useEffect(() => {
    if (!enabled) return;
    let start: { x: number; y: number } | null = null;
    const onStart = (event: TouchEvent) => {
      const touch = event.touches[0];
      const target = event.target instanceof Element ? event.target : null;
      start = null;
      if (!touch || !target || target.closest("input, textarea") || scrollsSideways(target)) return;
      // Open, only a swipe on the menu closes it; shut, none starts while another layer (Search,
      // Settings, a dialog) is open over the page, which would leave two.
      if (open ? !target.closest("[data-mobile=true]") : touch.clientX > EDGE || document.querySelector("[role=dialog][data-state=open]")) return;
      start = { x: touch.clientX, y: touch.clientY };
    };
    const onEnd = (event: TouchEvent) => {
      const touch = event.changedTouches[0];
      if (!start || !touch) return;
      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      start = null;
      if (Math.abs(dx) < DISTANCE || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      if (!open && dx > 0) setOpen(true);
      if (open && dx < 0) setOpen(false);
    };
    document.addEventListener("touchstart", onStart, { passive: true });
    document.addEventListener("touchend", onEnd, { passive: true });
    return () => {
      document.removeEventListener("touchstart", onStart);
      document.removeEventListener("touchend", onEnd);
    };
  }, [enabled, open, setOpen]);
}

/** Whether the touch began in something that scrolls sideways (a row of chips, a table): the
 * swipe is its. */
function scrollsSideways(from: Element): boolean {
  for (let element: Element | null = from; element && element !== document.body; element = element.parentElement) {
    const overflow = getComputedStyle(element).overflowX;
    if ((overflow === "auto" || overflow === "scroll") && element.scrollWidth > element.clientWidth) return true;
  }
  return false;
}
