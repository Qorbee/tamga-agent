"use client";

import { useCallback, useEffect, useRef, type RefObject } from "react";

function scrollerOf(element: HTMLElement | null): HTMLElement | null {
  for (let current = element; current; current = current.parentElement) {
    const { overflowY } = getComputedStyle(current);
    if (overflowY === "auto" || overflowY === "scroll") return current;
  }
  return null;
}

/**
 * Holds the chat where it is while a step opens or closes: the browser would otherwise move the
 * scroll as the height animates. assistant-ui's own lock also hides the scrollbar for the
 * animation, which made the bar vanish and come back and the page jump beside it; this one only
 * puts the scroll back, and never touches the bar.
 */
export function useKeepInPlace(ref: RefObject<HTMLElement | null>, duration: number): () => void {
  const release = useRef<(() => void) | null>(null);
  useEffect(() => () => release.current?.(), []);
  return useCallback(() => {
    release.current?.();
    const scroller = scrollerOf(ref.current);
    if (!scroller) return;
    const top = scroller.scrollTop;
    const hold = () => {
      if (scroller.scrollTop !== top) scroller.scrollTop = top;
    };
    scroller.addEventListener("scroll", hold);
    const timer = window.setTimeout(() => release.current?.(), duration);
    release.current = () => {
      scroller.removeEventListener("scroll", hold);
      window.clearTimeout(timer);
      release.current = null;
    };
  }, [ref, duration]);
}
