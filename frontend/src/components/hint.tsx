"use client";

import { useRef, useState, type ComponentProps, type MouseEvent, type ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useFinePointer } from "@/hooks/use-mobile";

// What does something of its own when tapped: its label is its accessible name, and a tooltip on a
// touch screen would only show over what was just chosen.
const CONTROL = "button, a, input, select, textarea, [role=button], [role=radio], [role=checkbox], [role=tab]";

/**
 * What an icon or an icon-only control means, in the app's own tooltip (never the browser's
 * `title`): on hover and on keyboard focus. On a touch screen a control shows none, its label being
 * its accessible name; an explanation on something that is not a control (a chip, a badge) opens
 * on a tap instead, and closes on the next tap anywhere. The child is the trigger, so it must take
 * a ref and props: an element or a `Button`.
 */
export function Hint({
  label,
  side = "bottom",
  children,
}: {
  label: string;
  side?: ComponentProps<typeof TooltipContent>["side"];
  children: ReactNode;
}) {
  const fine = useFinePointer();
  const [tapped, setTapped] = useState(false);
  // The tooltip closes itself on its trigger's click, right after the tap that opened it.
  const openedAt = useRef(0);
  const onClick = (event: MouseEvent<HTMLElement>) => {
    if (fine || event.currentTarget.matches(CONTROL)) return;
    openedAt.current = event.timeStamp;
    setTapped((open) => !open);
  };
  return (
    // Kept mounted either way, only held shut: wrapping the child or not would remount it.
    <Tooltip
      open={fine ? undefined : tapped}
      onOpenChange={
        fine
          ? undefined
          : (next) => {
              if (!next && performance.now() - openedAt.current > 100) setTapped(false);
            }
      }
    >
      <TooltipTrigger asChild onClick={onClick}>
        {children}
      </TooltipTrigger>
      <TooltipContent side={side}>{label}</TooltipContent>
    </Tooltip>
  );
}
