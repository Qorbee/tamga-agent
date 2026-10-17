"use client";

import type { ComponentProps, ReactNode } from "react";

import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetGrip, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";

/**
 * A small panel opened from a control: beside it on a desktop, a sheet from the screen's foot on a
 * phone, where a panel beside a control finger-wide has no room and covers what it changes. The
 * sheet is pulled down or tapped outside to close, and is titled, as a dialog must be.
 *
 * `anchor` places the panel by the control without the control opening it (it opens on the
 * pointer resting there, set by the caller); a phone has no pointer to rest, so it has no sheet.
 */
export function PopSheet({
  open,
  onOpenChange,
  trigger,
  title,
  children,
  anchor = false,
  align = "start",
  className,
  contentProps,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger: ReactNode;
  // The sheet's heading on a phone; a desktop panel shows its own if it wants one.
  title: string;
  children: ReactNode;
  anchor?: boolean;
  align?: "start" | "center" | "end";
  // The desktop panel's width and padding.
  className?: string;
  contentProps?: Omit<ComponentProps<typeof PopoverContent>, "className" | "align" | "children">;
}) {
  const mobile = useIsMobile();
  if (mobile) {
    if (anchor) return <>{trigger}</>;
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetTrigger asChild>{trigger}</SheetTrigger>
        <SheetContent
          side="bottom"
          showCloseButton={false}
          className="max-h-[85dvh] gap-0 overflow-y-auto rounded-t-3xl px-4 pt-2 pb-[max(1rem,env(safe-area-inset-bottom))]"
        >
          <SheetGrip onDismiss={() => onOpenChange(false)}>
            <SheetHeader className="px-1 pt-1 pb-3">
              <SheetTitle className="text-base text-pretty first-letter:uppercase">{title}</SheetTitle>
            </SheetHeader>
          </SheetGrip>
          {children}
        </SheetContent>
      </Sheet>
    );
  }
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      {anchor ? <PopoverAnchor asChild>{trigger}</PopoverAnchor> : <PopoverTrigger asChild>{trigger}</PopoverTrigger>}
      <PopoverContent align={align} collisionPadding={16} className={cn(className)} {...contentProps}>
        {children}
      </PopoverContent>
    </Popover>
  );
}
