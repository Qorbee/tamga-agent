"use client";

import { FitToScreenIcon, RotateClockwiseIcon, SearchAddIcon, SearchMinusIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useRef, type ReactNode, type RefObject } from "react";

import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

// Each press of + or − zooms by this much; a wheel step or a pinch by as much as it moved.
export const ZOOM_STEP = 1.25;

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Ctrl or ⌘ with the wheel, and a trackpad's pinch (which the browser sends as one), zooms by
 * `factor` around `point` (from the element's top left) instead of zooming the whole page.
 */
export function useWheelZoom(
  ref: RefObject<HTMLElement | null>,
  onZoom: (factor: number, point: { x: number; y: number }) => void,
): void {
  const latest = useRef(onZoom);
  useEffect(() => {
    latest.current = onZoom;
  });
  // React binds wheel listeners as passive, and only an active one can keep the page from zooming.
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      latest.current(clamp(Math.exp(-event.deltaY * 0.01), 0.5, 2), {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [ref]);
}

/**
 * The viewer's controls, floating over the bottom of the file: zoom out, the zoom (which fits the
 * file again), zoom in, fit, and turning a sideways scan upright. `children` go first: a PDF's page.
 */
export function ViewerToolbar({
  zoom,
  canZoomIn,
  canZoomOut,
  onZoomIn,
  onZoomOut,
  onFit,
  onRotate,
  className,
  children,
}: {
  zoom: number;
  canZoomIn: boolean;
  canZoomOut: boolean;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  onRotate?: () => void;
  className?: string;
  children?: ReactNode;
}) {
  const { t, locale } = useI18n();
  return (
    <div
      role="toolbar"
      aria-label={t.files.viewer.controls}
      className={cn(
        "bg-background/90 border-border text-foreground flex items-center gap-0.5 rounded-full border p-1 shadow-md backdrop-blur",
        className,
      )}
    >
      {children}
      <Button variant="ghost" size="icon-sm" className="rounded-full" disabled={!canZoomOut} onClick={onZoomOut}>
        <HugeiconsIcon icon={SearchMinusIcon} strokeWidth={1.75} />
        <span className="sr-only">{t.files.viewer.zoomOut}</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-8 min-w-14 rounded-full px-2 text-xs tabular-nums"
        onClick={onFit}
        title={t.files.viewer.fit}
      >
        {formatNumber(Math.round(zoom * 100), locale)}%
      </Button>
      <Button variant="ghost" size="icon-sm" className="rounded-full" disabled={!canZoomIn} onClick={onZoomIn}>
        <HugeiconsIcon icon={SearchAddIcon} strokeWidth={1.75} />
        <span className="sr-only">{t.files.viewer.zoomIn}</span>
      </Button>
      <Button variant="ghost" size="icon-sm" className="rounded-full" onClick={onFit}>
        <HugeiconsIcon icon={FitToScreenIcon} strokeWidth={1.75} />
        <span className="sr-only">{t.files.viewer.fit}</span>
      </Button>
      {onRotate ? (
        <Button variant="ghost" size="icon-sm" className="rounded-full" onClick={onRotate}>
          <HugeiconsIcon icon={RotateClockwiseIcon} strokeWidth={1.75} />
          <span className="sr-only">{t.files.viewer.rotate}</span>
        </Button>
      ) : null}
    </div>
  );
}
