"use client";

import { type RefObject, useEffect } from "react";

// How far a finger moves before a drag picks its direction, and how fast a flick must be (px per
// ms) to settle the drawer its way however far it went.
const SLOP = 10;
const FLICK = 0.4;
// The drawer's own glide, kept with the finger's: set inline while it follows, so a drawer
// let go mid-way glides on from where it is.
const EASE = "300ms cubic-bezier(0.32, 0.72, 0, 1)";
// Another layer open over the page: a Radix dialog or sheet, or a modal of our own (the image
// lightbox); the menu itself is left out.
const LAYER = "[role=dialog][data-state=open], [role=dialog][aria-modal=true]:not([data-mobile])";

/**
 * The phone's menu follows the finger, as the chat apps' does: a sideways drag anywhere on the
 * page pulls it out, one on the menu pushes it back, and letting go settles it open or shut by
 * where it is and how fast it moved. It is moved by `translate` alone, written straight to the
 * element on each move (no render), so it keeps the screen's frame rate. A drag that starts in a
 * field or in something that scrolls sideways (a row of chips, a table) is left to it; a mostly
 * vertical one is a scroll.
 */
export function useMenuDrag({
  panel,
  scrim,
  open,
  setOpen,
}: {
  panel: RefObject<HTMLElement | null>;
  scrim: RefObject<HTMLElement | null>;
  open: boolean;
  setOpen: (open: boolean) => void;
}) {
  useEffect(() => {
    const drawer = panel.current;
    const shade = scrim.current;
    if (!drawer || !shade) return;
    let start: { x: number; y: number } | null = null;
    let dragging = false;
    let width = 0;
    let at = 0;
    let settling = 0;
    const flick = flickMeter();

    const place = (shown: number) => {
      const x = (Math.min(1, Math.max(0, shown)) - 1) * width;
      // `translate`, not `transform`: the classes place it by `translate`, and the two would add up.
      drawer.style.translate = `${x}px 0`;
      shade.style.opacity = String(1 + x / width);
    };
    const release = () => {
      for (const element of [drawer, shade]) {
        element.style.transition = "";
        element.style.translate = "";
        element.style.opacity = "";
      }
    };

    const onStart = (event: TouchEvent) => {
      // A second finger ends the drag where it is, as letting go would.
      if (dragging) {
        onEnd();
        return;
      }
      start = null;
      const touch = event.touches[0];
      const target = event.target instanceof Element ? event.target : null;
      if (event.touches.length !== 1 || !touch || !target) return;
      if (target.closest("input, textarea, [contenteditable=true]") || scrollsSideways(target)) return;
      // Open, the drag is on the menu or what shows of the page beside it; shut, none starts
      // while another layer (Search, Settings, a dialog) is over the page.
      if (open ? !drawer.contains(target) && target !== shade : document.querySelector(LAYER)) return;
      start = { x: touch.clientX, y: touch.clientY };
    };
    const onMove = (event: TouchEvent) => {
      const touch = event.touches[0];
      if (!start || !touch) return;
      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      if (!dragging) {
        if (Math.abs(dx) < SLOP && Math.abs(dy) < SLOP) return;
        // The first clear move decides: sideways, toward where the menu can go, drags it.
        if (Math.abs(dx) < Math.abs(dy) * 1.2 || (open ? dx > 0 : dx < 0)) {
          start = null;
          return;
        }
        dragging = true;
        window.clearTimeout(settling);
        width = drawer.offsetWidth;
        start = { x: touch.clientX, y: touch.clientY };
        at = touch.clientX;
        flick.reset(at);
        drawer.style.transition = "none";
        shade.style.transition = "none";
      }
      if (event.cancelable) event.preventDefault();
      at = touch.clientX;
      flick.track(at);
      place(((open ? width : 0) + at - start.x) / width);
    };
    const onEnd = () => {
      if (!dragging || !start) {
        start = null;
        return;
      }
      const shown = ((open ? width : 0) + at - start.x) / width;
      const speed = flick.speed();
      start = null;
      dragging = false;
      const next = speed > FLICK ? true : speed < -FLICK ? false : shown > 0.5;
      drawer.style.transition = `translate ${EASE}`;
      shade.style.transition = "opacity 300ms ease";
      // Glide from where the finger left it, then hand the place back to the classes (by time,
      // not transitionend, which never fires when it is already there).
      place(next ? 1 : 0);
      settling = window.setTimeout(release, 320);
      if (next !== open) setOpen(next);
    };
    const onCancel = () => {
      if (dragging) release();
      start = null;
      dragging = false;
    };

    document.addEventListener("touchstart", onStart, { passive: true });
    // Not passive: a sideways drag keeps the page from scrolling under it.
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend", onEnd, { passive: true });
    document.addEventListener("touchcancel", onCancel, { passive: true });
    return () => {
      document.removeEventListener("touchstart", onStart);
      document.removeEventListener("touchmove", onMove);
      document.removeEventListener("touchend", onEnd);
      document.removeEventListener("touchcancel", onCancel);
      window.clearTimeout(settling);
      release();
    };
  }, [panel, scrim, open, setOpen]);
}

/**
 * A bottom sheet the finger pulls down to close: dragged from `grip` (its handle and title), it
 * follows the finger down and, let go far enough or fast enough, closes; otherwise it springs
 * back. Pointer events on the grip alone, so the sheet's own list still scrolls.
 */
export function useSheetDrag({ grip, onDismiss }: { grip: RefObject<HTMLElement | null>; onDismiss: () => void }) {
  useEffect(() => {
    const handle = grip.current;
    const sheet = handle?.closest<HTMLElement>("[data-slot=sheet-content]");
    if (!handle || !sheet) return;
    let start: number | null = null;
    const flick = flickMeter();

    const onDown = (event: PointerEvent) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      start = event.clientY;
      flick.reset(event.clientY);
      handle.setPointerCapture(event.pointerId);
      sheet.style.transition = "none";
    };
    const onMove = (event: PointerEvent) => {
      if (start === null) return;
      flick.track(event.clientY);
      // Up, it barely gives; down, it follows the finger.
      const dy = event.clientY - start;
      sheet.style.transform = `translate3d(0, ${dy > 0 ? dy : dy / 8}px, 0)`;
    };
    const onUp = (event: PointerEvent) => {
      if (start === null) return;
      const dy = event.clientY - start;
      start = null;
      if (dy > sheet.offsetHeight / 3 || flick.speed() > FLICK) {
        // The sheet leaves from where it is: its closing animation starts at this transform.
        sheet.style.transition = "";
        onDismiss();
        return;
      }
      sheet.style.transition = `transform ${EASE}`;
      sheet.style.transform = "";
      window.setTimeout(() => (sheet.style.transition = ""), 320);
    };

    handle.addEventListener("pointerdown", onDown);
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
    return () => {
      handle.removeEventListener("pointerdown", onDown);
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
    };
  }, [grip, onDismiss]);
}

/** The finger's speed over its last moves, in px per ms along one axis: a flick settles by it. */
function flickMeter() {
  let last = { at: 0, time: 0 };
  let speed = 0;
  return {
    reset(at: number) {
      last = { at, time: performance.now() };
      speed = 0;
    },
    track(at: number) {
      const time = performance.now();
      const elapsed = time - last.time;
      if (elapsed > 0) speed = 0.7 * ((at - last.at) / elapsed) + 0.3 * speed;
      last = { at, time };
    },
    // A finger held still before letting go has no flick left.
    speed: () => (performance.now() - last.time > 100 ? 0 : speed),
  };
}

/** Whether the touch began in something that moves sideways itself: a row of chips or a table
 * that scrolls, or a surface with gestures of its own (`touch-action: none`, the image viewer's
 * pan). The drag is its. */
function scrollsSideways(from: Element): boolean {
  for (let element: Element | null = from; element && element !== document.body; element = element.parentElement) {
    const style = getComputedStyle(element);
    if (style.touchAction === "none") return true;
    const overflow = style.overflowX;
    if ((overflow === "auto" || overflow === "scroll") && element.scrollWidth > element.clientWidth) return true;
  }
  return false;
}
