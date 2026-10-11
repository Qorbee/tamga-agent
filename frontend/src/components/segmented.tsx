"use client";

import type { ReactNode } from "react";

import { Hint } from "@/components/hint";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

/**
 * The app's one selector for a few choices (a theme, a way to pay, a list's view): a track with the
 * chosen option raised off it, filled, edged and in full ink, the others receding, so which is
 * chosen reads at a glance in either theme. `multiple` lets more than one be chosen; a click on
 * the only chosen option leaves it chosen, as a selector never ends with nothing. A single one
 * may show nothing chosen (`null`) while another control decides what the page shows.
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
    "bg-muted border-border rounded-xl border p-0.5",
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
      type="single"
      spacing={0.5}
      value={props.value ?? ""}
      aria-label={label}
      className={track}
      onValueChange={(next) => {
        if (next) props.onChange(next as T);
      }}
    >
      {items}
    </ToggleGroup>
  );
}
