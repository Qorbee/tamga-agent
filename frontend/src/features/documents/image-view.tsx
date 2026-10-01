"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

import { clamp, ViewerToolbar, ZOOM_STEP } from "./viewer-toolbar";

type Size = { width: number; height: number };
type Point = { x: number; y: number };
// `scale` is image pixels to screen pixels; null fits the image in the panel, and keeps fitting it
// as the panel is resized. The offset moves the image's centre from the panel's.
type View = { scale: number | null; rotation: number; x: number; y: number };
type Gesture =
  | { kind: "pan"; start: Point; origin: Point }
  | { kind: "pinch"; distance: number; scale: number; middle: Point; origin: Point };

const FITTED: View = { scale: null, rotation: 0, x: 0, y: 0 };
const MAX_SCALE = 8;
// Arrow keys move a zoomed image by this many pixels.
const KEY_PAN = 48;

/**
 * A photo or a scan: fitted to the panel, zoomed with the buttons, the wheel with Ctrl (a trackpad's
 * pinch), two fingers or a double click, moved by dragging, and turned upright a quarter at a time.
 */
export function ImageView({ url, name, onFailed }: { url: string; name: string; onFailed: () => void }) {
  const { t } = useI18n();
  const frame = useRef<HTMLDivElement>(null);
  const box = useSize(frame);
  const [natural, setNatural] = useState<Size | null>(null);
  const [view, setView] = useState<View>(FITTED);
  const pointers = useRef(new Map<number, Point>());
  const gesture = useRef<Gesture | null>(null);

  const turned = view.rotation % 180 !== 0;
  const drawn = natural && { width: turned ? natural.height : natural.width, height: turned ? natural.width : natural.height };
  // Never enlarged past its own pixels to fit: a small photo shows at its size.
  const fit = drawn && box ? Math.min(1, box.width / drawn.width, box.height / drawn.height) : 1;
  const scale = view.scale ?? fit;
  const min = Math.min(fit, 1) / 2;
  const max = Math.max(fit, MAX_SCALE);
  // How far the image can move before an edge comes inside the panel; none while it fits.
  const room = {
    x: drawn && box ? Math.max(0, (drawn.width * scale - box.width) / 2) : 0,
    y: drawn && box ? Math.max(0, (drawn.height * scale - box.height) / 2) : 0,
  };
  const x = clamp(view.x, -room.x, room.x);
  const y = clamp(view.y, -room.y, room.y);
  const movable = room.x > 0 || room.y > 0;

  // Latest values for the listeners below, which are bound once.
  const latest = useRef({ scale, min, max, x, y, box });
  useEffect(() => {
    latest.current = { scale, min, max, x, y, box };
  });

  /** Zooms to `next` keeping the point `around` (from the panel's centre) where it is on screen. */
  const zoomTo = (next: number, around: Point = { x: 0, y: 0 }, from = latest.current) => {
    const target = clamp(next, from.min, from.max);
    const ratio = target / from.scale;
    setView((current) => ({
      ...current,
      scale: target,
      x: around.x - (around.x - from.x) * ratio,
      y: around.y - (around.y - from.y) * ratio,
    }));
  };
  const fromCentre = (clientX: number, clientY: number): Point => {
    const rect = frame.current?.getBoundingClientRect();
    return rect ? { x: clientX - rect.left - rect.width / 2, y: clientY - rect.top - rect.height / 2 } : { x: 0, y: 0 };
  };

  // React binds wheel listeners as passive, and only an active one can keep the page from zooming.
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      const now = latest.current;
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        const factor = clamp(Math.exp(-event.deltaY * 0.01), 0.5, 2);
        const rect = element.getBoundingClientRect();
        zoomTo(now.scale * factor, {
          x: event.clientX - rect.left - rect.width / 2,
          y: event.clientY - rect.top - rect.height / 2,
        });
      } else if (element.dataset.movable === "true") {
        event.preventDefault();
        setView((current) => ({ ...current, x: now.x - event.deltaX, y: now.y - event.deltaY }));
      }
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
    // Bound once: zoomTo reads only the latest values and setState.
  }, []);

  const startGesture = () => {
    const [first, second] = [...pointers.current.values()];
    if (first && second) {
      const middle = fromCentre((first.x + second.x) / 2, (first.y + second.y) / 2);
      gesture.current = {
        kind: "pinch",
        distance: Math.hypot(first.x - second.x, first.y - second.y) || 1,
        scale,
        middle,
        origin: { x, y },
      };
    } else if (first) {
      gesture.current = { kind: "pan", start: first, origin: { x, y } };
    } else gesture.current = null;
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    startGesture();
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const now = gesture.current;
    if (now?.kind === "pan") {
      setView((current) => ({
        ...current,
        x: now.origin.x + event.clientX - now.start.x,
        y: now.origin.y + event.clientY - now.start.y,
      }));
    } else if (now?.kind === "pinch") {
      const [first, second] = [...pointers.current.values()];
      if (!first || !second) return;
      const distance = Math.hypot(first.x - second.x, first.y - second.y);
      zoomTo(now.scale * (distance / now.distance), now.middle, {
        ...latest.current,
        scale: now.scale,
        x: now.origin.x,
        y: now.origin.y,
      });
    }
  };
  const onPointerEnd = (event: PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId);
    startGesture();
  };

  const rotate = () => setView((current) => ({ ...FITTED, rotation: (current.rotation + 90) % 360 }));
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, Point> = {
      ArrowLeft: { x: KEY_PAN, y: 0 },
      ArrowRight: { x: -KEY_PAN, y: 0 },
      ArrowUp: { x: 0, y: KEY_PAN },
      ArrowDown: { x: 0, y: -KEY_PAN },
    };
    const move = moves[event.key];
    if (event.key === "+" || event.key === "=") zoomTo(scale * ZOOM_STEP);
    else if (event.key === "-") zoomTo(scale / ZOOM_STEP);
    else if (event.key === "0") setView((current) => ({ ...FITTED, rotation: current.rotation }));
    else if (event.key === "r") rotate();
    else if (move && movable) setView((current) => ({ ...current, x: x + move.x, y: y + move.y }));
    else return;
    event.preventDefault();
  };

  return (
    <div className="bg-muted/50 relative h-full min-h-72 overflow-hidden">
      <div
        ref={frame}
        tabIndex={0}
        role="img"
        aria-label={name}
        aria-roledescription={t.files.viewer.image}
        data-movable={movable}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        onDoubleClick={(event) => {
          if (scale > fit * 1.01) setView((current) => ({ ...FITTED, rotation: current.rotation }));
          else zoomTo(Math.max(fit * 2, 1), fromCentre(event.clientX, event.clientY));
        }}
        onKeyDown={onKeyDown}
        className={cn(
          "focus-visible:ring-ring/50 absolute inset-0 touch-none outline-none select-none focus-visible:ring-2 focus-visible:ring-inset",
          movable ? "cursor-grab active:cursor-grabbing" : "cursor-zoom-in",
        )}
      >
        {/* A blob of a verified image: next/image cannot take one. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={url}
          alt=""
          draggable={false}
          onLoad={(event) =>
            setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })
          }
          onError={onFailed}
          style={{
            width: natural ? natural.width * scale : undefined,
            height: natural ? natural.height * scale : undefined,
            transform: `translate(-50%, -50%) translate(${x}px, ${y}px) rotate(${view.rotation}deg)`,
          }}
          className={cn(
            "pointer-events-none absolute top-1/2 left-1/2 max-w-none rounded-sm shadow-sm",
            !natural && "invisible",
          )}
        />
      </div>
      <ViewerToolbar
        zoom={scale}
        canZoomIn={scale < max}
        canZoomOut={scale > min}
        onZoomIn={() => zoomTo(scale * ZOOM_STEP)}
        onZoomOut={() => zoomTo(scale / ZOOM_STEP)}
        onFit={() => setView((current) => ({ ...FITTED, rotation: current.rotation }))}
        onRotate={rotate}
        className="absolute bottom-3 left-1/2 -translate-x-1/2"
      />
    </div>
  );
}

/** The element's size, as it changes. */
function useSize(ref: React.RefObject<HTMLElement | null>): Size | null {
  const [size, setSize] = useState<Size | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}
