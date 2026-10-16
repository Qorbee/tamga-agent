"use client";

import { type KeyboardEvent, type PointerEvent, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * A slider over a few named steps: a thick track filled up to a round thumb, with faint dots on
 * the other steps. A tap on the track or a drag moves it to the nearest step, and the arrow keys,
 * Home and End do too; each step it lands on is saved at once. `small` is the size for the end of
 * a menu row.
 */
export function StepSlider<T extends string>({
  label,
  steps,
  value,
  name,
  onChange,
  small = false,
  className,
}: {
  label: string;
  steps: readonly T[];
  value: T;
  // What the step is called, read out with it.
  name: (step: T) => string;
  onChange: (step: T) => void;
  small?: boolean;
  className?: string;
}) {
  const track = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const at = Math.max(0, steps.indexOf(value));
  const last = steps.length - 1;
  const share = (index: number) => (last === 0 ? 0 : (index / last) * 100);

  const go = (index: number) => {
    const step = steps[Math.min(last, Math.max(0, index))];
    if (step !== undefined && step !== value) onChange(step);
  };
  const nearest = (x: number) => {
    const box = track.current?.getBoundingClientRect();
    if (!box || box.width === 0) return at;
    const along = (x - box.left) / box.width;
    // A right-to-left page runs the steps the other way.
    const forward = getComputedStyle(track.current as Element).direction === "rtl" ? 1 - along : along;
    return Math.round(Math.min(1, Math.max(0, forward)) * last);
  };
  const down = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
    go(nearest(event.clientX));
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    if (dragging) go(nearest(event.clientX));
  };
  const key = (event: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, number> = {
      ArrowRight: at + 1,
      ArrowUp: at + 1,
      ArrowLeft: at - 1,
      ArrowDown: at - 1,
      Home: 0,
      End: last,
    };
    const to = moves[event.key];
    if (to === undefined) return;
    event.preventDefault();
    go(to);
  };

  return (
    <div
      role="slider"
      tabIndex={0}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={last}
      aria-valuenow={at}
      aria-valuetext={name(value)}
      onKeyDown={key}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={() => setDragging(false)}
      onPointerCancel={() => setDragging(false)}
      className={cn(
        "focus-visible:ring-ring group relative flex cursor-pointer touch-none items-center rounded-full outline-none select-none focus-visible:ring-2",
        // Room for half the thumb at each end, so it never hangs past the track.
        small ? "h-6 w-28 px-2.5" : "h-9 w-full px-3.5",
        className,
      )}
    >
      <div ref={track} className={cn("bg-input relative w-full rounded-full", small ? "h-1.5" : "h-2.5")}>
        {/* Filled up to the thumb's middle, so the colour runs under it. */}
        <span
          className={cn("bg-primary absolute inset-y-0 start-0 rounded-full", !dragging && "transition-[width] duration-150")}
          style={{ width: `${share(at)}%` }}
          aria-hidden
        />
        {steps.map((step, index) =>
          index === at ? null : (
            <span
              key={step}
              className={cn(
                "absolute top-1/2 size-1 -translate-x-1/2 -translate-y-1/2 rounded-full rtl:translate-x-1/2",
                index < at ? "bg-primary-foreground/60" : "bg-muted-foreground/45",
              )}
              style={{ insetInlineStart: `${share(index)}%` }}
              aria-hidden
            />
          ),
        )}
        <span
          className={cn(
            "bg-primary-foreground border-border absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border shadow-sm rtl:translate-x-1/2",
            small ? "size-4" : "size-7",
            !dragging && "transition-[inset-inline-start] duration-150",
          )}
          style={{ insetInlineStart: `${share(at)}%` }}
          aria-hidden
        />
      </div>
    </div>
  );
}
