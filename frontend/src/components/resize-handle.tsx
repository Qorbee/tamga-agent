"use client";

import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

import { clampSize, type PanelSize } from "@/lib/panel-sizes";
import { cn } from "@/lib/utils";

const KEY_STEP = 16;
// How far past the minimum a drag must go before it collapses the panel: holding at the minimum,
// or overshooting it a little, keeps the panel open.
const COLLAPSE_SLACK = 72;

/**
 * The draggable edge of a panel: drag it, or focus it and use the arrow keys; a double click puts
 * the initial width back. `edge` is the side of the panel it sits on, so a left panel's handle is on
 * its right edge and grows the panel as it moves right. With `onCollapse`, dragging well past the
 * minimum collapses the panel, and it keeps the width it had before the drag for when it reopens.
 */
export function ResizeHandle({
  label,
  size,
  width,
  edge,
  onResize,
  onResizeEnd,
  onCollapse,
  className,
}: {
  label: string;
  size: PanelSize;
  width: number;
  edge: "left" | "right";
  onResize: (width: number) => void;
  /** Once the drag or key press is over, with the width it settled on. */
  onResizeEnd: (width: number) => void;
  onCollapse?: () => void;
  className?: string;
}) {
  const drag = useRef<{ x: number; from: number; to: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const direction = edge === "right" ? 1 : -1;

  function settle(next: number) {
    onResize(next);
    onResizeEnd(next);
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    // Stops the drag from selecting the text it passes over.
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { x: event.clientX, from: width, to: width };
    setDragging(true);
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    const wanted = drag.current.from + (event.clientX - drag.current.x) * direction;
    if (onCollapse && wanted < size.min - COLLAPSE_SLACK) {
      const { from } = drag.current;
      drag.current = null;
      setDragging(false);
      event.currentTarget.releasePointerCapture(event.pointerId);
      settle(from);
      onCollapse();
      return;
    }
    const next = clampSize(size, wanted);
    drag.current.to = next;
    onResize(next);
  }

  function onPointerEnd(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    const { to } = drag.current;
    drag.current = null;
    setDragging(false);
    event.currentTarget.releasePointerCapture(event.pointerId);
    onResizeEnd(to);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const target = {
      ArrowLeft: width - KEY_STEP * direction,
      ArrowRight: width + KEY_STEP * direction,
      Home: size.min,
      End: size.max,
    }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    settle(clampSize(size, target));
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={size.min}
      aria-valuemax={size.max}
      aria-valuenow={width}
      tabIndex={0}
      data-dragging={dragging || undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
      onDoubleClick={() => settle(size.initial)}
      onKeyDown={onKeyDown}
      className={cn(
        "absolute inset-y-0 z-20 w-3 cursor-col-resize touch-none outline-none select-none",
        edge === "right" ? "-right-1.5" : "-left-1.5",
        // The visible line: nothing at rest, a hairline on hover, stronger while dragged or focused.
        "after:absolute after:inset-y-0 after:left-1/2 after:w-0.5 after:-translate-x-1/2 after:rounded-full after:transition-colors after:delay-75",
        "hover:after:bg-foreground/15 focus-visible:after:bg-ring data-dragging:after:bg-foreground/25",
        className,
      )}
    />
  );
}
