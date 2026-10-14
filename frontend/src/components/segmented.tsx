"use client";

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { Hint } from "@/components/hint";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

/**
 * The app's one selector for a few choices (a theme, a way to pay, a list's view): a track with the
 * chosen option raised off it, filled, edged and in full ink, the others receding, so which is
 * chosen reads at a glance in either theme. `multiple` lets more than one be chosen; a click on
 * the only chosen option leaves it chosen, as a selector never ends with nothing. A single one
 * may show nothing chosen (`null`) while another control decides what the page shows. A single
 * one's raised part slides to the chosen option, as a phone's selectors do.
 */
export type SegmentOption<T extends string> = {
  value: T;
  label: ReactNode;
  /** Named in a tooltip and to a screen reader, for an option that shows an icon or a count. */
  hint?: string;
  lang?: string;
  className?: string;
};

type Common<T extends string> = {
  options: readonly SegmentOption<T>[];
  label: string;
  /** Stretch across the row, each option an equal share (a page's views). */
  fill?: boolean;
  size?: "sm" | "default";
  disabled?: boolean;
  className?: string;
};

export function Segmented<T extends string>(
  props: Common<T> &
    (
      | { multiple?: false; value: T | null; onChange: (value: T) => void }
      | { multiple: true; value: readonly T[]; onChange: (value: T[]) => void }
    ),
) {
  const { options, label, fill = false, size = "sm", disabled, className } = props;
  const single = !props.multiple;
  const { track: trackRef, place, sliding } = useSlide(single ? (props.value ?? null) : null);
  const items = options.map((option) => {
    const item = (
      <ToggleGroupItem
        key={option.value}
        value={option.value}
        lang={option.lang}
        aria-label={option.hint}
        disabled={disabled}
        // Styled by the item's own `aria-checked` (single) or `aria-pressed` (multiple): a hint
        // around it takes over `data-state` with the tooltip's.
        className={cn(
          "text-muted-foreground hover:text-foreground h-auto min-h-7 rounded-[0.6rem] border border-transparent px-3 py-1 hover:bg-transparent",
          "aria-checked:bg-segment aria-checked:border-segment-border aria-checked:text-foreground aria-checked:shadow-xs",
          "aria-pressed:bg-segment aria-pressed:border-segment-border aria-pressed:text-foreground aria-pressed:shadow-xs",
          // Once the sliding part is placed, it is the chosen option's ground.
          place && "relative aria-checked:border-transparent aria-checked:bg-transparent aria-checked:shadow-none",
          size === "default" && "min-h-8",
          fill && "min-w-0 flex-1 px-2",
          option.className,
        )}
      >
        {option.label}
      </ToggleGroupItem>
    );
    return option.hint ? (
      <Hint key={option.value} label={option.hint}>
        {item}
      </Hint>
    ) : (
      item
    );
  });
  const track = cn(
    "bg-muted border-border relative rounded-xl border p-0.5",
    fill ? "flex w-full" : "w-fit max-w-full flex-wrap",
    className,
  );
  return props.multiple ? (
    <ToggleGroup
      type="multiple"
      spacing={0.5}
      value={[...props.value]}
      aria-label={label}
      className={track}
      onValueChange={(next) => {
        if (next.length > 0) props.onChange(next as T[]);
      }}
    >
      {items}
    </ToggleGroup>
  ) : (
    <ToggleGroup
      ref={trackRef}
      type="single"
      spacing={0.5}
      value={props.value ?? ""}
      aria-label={label}
      className={track}
      onValueChange={(next) => {
        if (next) props.onChange(next as T);
      }}
    >
      {place ? (
        <span
          aria-hidden
          className={cn(
            "bg-segment border-segment-border pointer-events-none absolute top-0 left-0 rounded-[0.6rem] border shadow-xs",
            sliding && "transition-[translate,width,height] duration-250 ease-out motion-reduce:transition-none",
          )}
          style={{ translate: `${place.x}px ${place.y}px`, width: place.width, height: place.height }}
        />
      ) : null}
      {items}
    </ToggleGroup>
  );
}

type Place = { x: number; y: number; width: number; height: number };

/**
 * Where the chosen option sits in its track, measured after layout and again when the track
 * changes size; `sliding` turns on only after the first placement, so a selector that appears
 * never slides in from the corner.
 */
function useSlide(value: string | null) {
  const track = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<Place | null>(null);
  const [sliding, setSliding] = useState(false);
  useLayoutEffect(() => {
    const root = track.current;
    if (!root) return;
    const measure = () => {
      const chosen = root.querySelector<HTMLElement>("[aria-checked=true]");
      setPlace(
        chosen
          ? { x: chosen.offsetLeft, y: chosen.offsetTop, width: chosen.offsetWidth, height: chosen.offsetHeight }
          : null,
      );
    };
    measure();
    const frame = requestAnimationFrame(() => setSliding(true));
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    for (const child of Array.from(root.children)) observer.observe(child);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [value]);
  return { track, place, sliding };
}
