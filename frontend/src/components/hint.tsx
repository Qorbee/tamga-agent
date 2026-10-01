"use client";

import type { ComponentProps, ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * What an icon or an icon-only control means, in the app's own tooltip (never the browser's
 * `title`): on hover and on keyboard focus. The child is the trigger, so it must take a ref and
 * props: an element or a `Button`.
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
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side}>{label}</TooltipContent>
    </Tooltip>
  );
}
