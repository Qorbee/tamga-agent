"use client";

import { RefreshIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { useI18n } from "@/lib/i18n/client";

// How far the finger pulls before letting go refreshes, the most the mark follows it, where it
// rests while the page is read again, and how fast it gives (the pull is damped).
const PULL = 64;
const MOST = 96;
const REST = 56;
const GIVE = 0.5;
const SLOP = 10;

/**
 * Pull to refresh, as a phone's apps do: a pull down from the top of whatever scrolls brings a
 * mark down with the finger, turning as it comes; let go past the mark and every query on screen
 * is read again, the mark spinning until they are. The app turns the browser's own pull off
 * (`overscroll-behavior: none`), which only reloaded the page. Each move is written to the mark,
 * not rendered. A pull that starts in a field, in the menu or under a dialog is left alone.
 */
export function PullRefresh() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const mark = useRef<HTMLDivElement>(null);
  const icon = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const ring = mark.current;
    const turn = icon.current;
    if (!ring || !turn) return;
    let start: { x: number; y: number } | null = null;
    let pulling = false;
    let busy = false;
    let pulled = 0;

    const show = (y: number, animate: boolean) => {
      ring.style.transition = animate ? "translate 250ms cubic-bezier(0.32, 0.72, 0, 1), opacity 200ms" : "none";
      ring.style.translate = `-50% ${y - 48}px`;
      ring.style.opacity = y > 4 ? "1" : "0";
      turn.style.rotate = `${(y / PULL) * 270}deg`;
    };

    const onStart = (event: TouchEvent) => {
      // A second finger ends a pull where it is, without refreshing.
      if (pulling) show(0, true);
      start = null;
      pulling = false;
      const touch = event.touches[0];
      const target = event.target instanceof Element ? event.target : null;
      if (busy || event.touches.length !== 1 || !touch || !target) return;
      if (target.closest("input, textarea, [contenteditable=true], [data-mobile=true], [role=dialog]")) return;
      if (!atTop(target)) return;
      start = { x: touch.clientX, y: touch.clientY };
    };
    const onMove = (event: TouchEvent) => {
      const touch = event.touches[0];
      if (!start || !touch) return;
      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      if (!pulling) {
        if (Math.abs(dx) < SLOP && Math.abs(dy) < SLOP) return;
        // Down, and mostly down: anything else is a scroll or the menu's drag.
        if (dy <= 0 || Math.abs(dy) < Math.abs(dx) * 1.2) {
          start = null;
          return;
        }
        pulling = true;
        start = { x: touch.clientX, y: touch.clientY };
      }
      if (event.cancelable) event.preventDefault();
      pulled = Math.min(MOST, Math.max(0, (touch.clientY - start.y) * GIVE));
      show(pulled, false);
    };
    const onEnd = () => {
      if (!pulling) {
        start = null;
        return;
      }
      start = null;
      pulling = false;
      if (pulled < PULL) {
        show(0, true);
        return;
      }
      busy = true;
      show(REST, true);
      turn.classList.add("animate-spin");
      // Everything on screen read again, and long enough that the spin is seen.
      void Promise.all([
        queryClient.refetchQueries({ type: "active" }),
        new Promise((resolve) => window.setTimeout(resolve, 600)),
      ]).finally(() => {
        busy = false;
        turn.classList.remove("animate-spin");
        show(0, true);
      });
    };

    document.addEventListener("touchstart", onStart, { passive: true });
    // Not passive: a pull keeps the page from moving under it.
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend", onEnd, { passive: true });
    document.addEventListener("touchcancel", onEnd, { passive: true });
    return () => {
      document.removeEventListener("touchstart", onStart);
      document.removeEventListener("touchmove", onMove);
      document.removeEventListener("touchend", onEnd);
      document.removeEventListener("touchcancel", onEnd);
    };
  }, [queryClient]);

  return (
    <div
      ref={mark}
      role="status"
      aria-label={t.shell.refreshing}
      className="bg-card border-border text-muted-foreground pointer-events-none fixed top-0 left-1/2 z-40 flex size-9 items-center justify-center rounded-full border opacity-0 shadow-md"
      style={{ translate: "-50% -48px" }}
    >
      <span ref={icon} className="flex">
        <HugeiconsIcon icon={RefreshIcon} strokeWidth={2} className="size-4.5" />
      </span>
    </div>
  );
}

/** Whether nothing the touch is in has been scrolled down: a pull there is a pull, not a scroll
 * back up. */
function atTop(from: Element): boolean {
  for (let element: Element | null = from; element && element !== document.body; element = element.parentElement) {
    const overflow = getComputedStyle(element).overflowY;
    if ((overflow === "auto" || overflow === "scroll") && element.scrollTop > 0) return false;
  }
  return window.scrollY <= 0;
}
