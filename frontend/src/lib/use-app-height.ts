"use client";

import { useEffect } from "react";

/**
 * Keeps `--app-height` on the root as tall as what the browser shows, the keyboard taken off:
 * Safari on iOS lays the page out behind its keyboard and only moves what is seen, so a frame
 * as tall as the screen would leave its composer under the keys. Android shrinks the layout
 * itself (the root layout's `interactiveWidget`), and there it reads the same.
 */
export function useAppHeight(): void {
  useEffect(() => {
    const view = window.visualViewport;
    if (!view) return;
    const root = document.documentElement;
    const fit = () => {
      root.style.setProperty("--app-height", `${Math.round(view.height)}px`);
      // Safari scrolls the page to bring a field into view; the frame is already in view.
      if (window.scrollY !== 0) window.scrollTo(0, 0);
    };
    fit();
    view.addEventListener("resize", fit);
    view.addEventListener("scroll", fit);
    return () => {
      view.removeEventListener("resize", fit);
      view.removeEventListener("scroll", fit);
      root.style.removeProperty("--app-height");
    };
  }, []);
}
